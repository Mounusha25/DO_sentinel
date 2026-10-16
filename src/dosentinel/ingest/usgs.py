"""Client for the USGS Water Data OGC API (continuous / instantaneous values).

Field names follow the published queryables of the `continuous` collection:
time, value (string), approval_status (Provisional | Approved), qualifier,
unit_of_measure, parameter_code, monitoring_location_id, time_series_id.

Provisional data is revised later, so we keep `approval_status` and
`last_modified`-style provenance and let downstream code re-pull recent windows.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterable, Iterator

import pandas as pd
import requests

BASE_URL = "https://api.waterdata.usgs.gov/ogcapi/v0"

COLUMNS = [
    "monitoring_location_id",
    "parameter_code",
    "time_series_id",
    "time",
    "value",
    "unit",
    "approval_status",
    "qualifier",
]


class UsgsClient:
    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        max_retries: int = 5,
        backoff: float = 2.0,
        timeout: float = 60.0,
        max_retry_after: float = 900.0,
    ) -> None:
        # An API key is optional but raises rate limits (check USGS docs for the
        # current header/param name; X-Api-Key is what we send here).
        # a key pasted into a secrets form often carries a trailing newline, which is an illegal header value
        self.api_key = (api_key or os.environ.get("USGS_API_KEY") or "").strip() or None
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff = backoff
        self.timeout = timeout
        self.max_retry_after = max_retry_after

    def _get(self, url: str, params: dict | None = None) -> dict:
        headers = {"Accept": "application/geo+json, application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        last = ""
        for attempt in range(self.max_retries):
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {resp.status_code}"
                ra = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                if ra:
                    last += f", Retry-After {ra}s"
                delay = self.backoff**attempt
                if resp.status_code == 429:
                    delay = max(delay, self._retry_after(resp))
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"USGS API still failing after {self.max_retries} attempts ({last}): {url}")

    def _retry_after(self, resp: requests.Response) -> float:
        """Seconds the server asked us to wait (capped), or 0 if absent/unparseable."""
        try:
            wait = float(resp.headers.get("Retry-After", 0))
        except (TypeError, ValueError):
            return 0.0
        return min(max(wait, 0.0), self.max_retry_after)

    def iter_features(self, collection: str, params: dict) -> Iterator[dict]:
        """Yield GeoJSON features, following `rel=next` pagination links."""
        url: str | None = f"{BASE_URL}/collections/{collection}/items"
        query: dict | None = {"f": "json", **params}
        while url:
            payload = self._get(url, query)
            yield from payload.get("features", [])
            url = next(
                (link["href"] for link in payload.get("links", []) if link.get("rel") == "next"),
                None,
            )
            query = None  # the next link is already a complete URL

    def fetch_continuous(
        self,
        site: str,
        parameter_code: str,
        start: str,
        end: str,
        limit: int = 10000,
    ) -> pd.DataFrame:
        """Continuous values for one site/parameter. `start`/`end` are RFC 3339 UTC."""
        params = {
            "monitoring_location_id": site,
            "parameter_code": parameter_code,
            "time": f"{start}/{end}",
            "limit": limit,
        }
        return features_to_frame(self.iter_features("continuous", params))


def features_to_frame(features: Iterable[dict]) -> pd.DataFrame:
    rows = []
    for f in features:
        p = f.get("properties", {})
        rows.append(
            {
                "monitoring_location_id": p.get("monitoring_location_id"),
                "parameter_code": p.get("parameter_code"),
                "time_series_id": p.get("time_series_id"),
                "time": p.get("time"),
                "value": p.get("value"),
                "unit": p.get("unit_of_measure"),
                "approval_status": p.get("approval_status"),
                "qualifier": p.get("qualifier"),
            }
        )
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True)
    # Values arrive as strings; blanks or non-numeric become NaN rather than errors.
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    # Approved values supersede provisional ones; ties keep input order (stable sort).
    df["_approved"] = df["approval_status"].eq("Approved")
    df = (
        df.sort_values(["time_series_id", "time", "_approved"], kind="stable")
        .drop_duplicates(["time_series_id", "time"], keep="last")
        .drop(columns="_approved")
        .reset_index(drop=True)
    )
    return df
