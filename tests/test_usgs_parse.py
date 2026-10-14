"""Parser tests. The fixture is hand-written to match the documented field names of
the `continuous` collection; it is NOT a captured USGS response."""
import pandas as pd

from dosentinel.ingest import features_to_frame


def _feat(t, v, status="Provisional", q=None, ts="ts-1"):
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "monitoring_location_id": "USGS-00000000",
            "parameter_code": "00300",
            "time_series_id": ts,
            "time": t,
            "value": v,
            "unit_of_measure": "mg/L",
            "approval_status": status,
            "qualifier": q,
        },
    }


def test_basic_parse_and_types():
    df = features_to_frame(
        [_feat("2026-01-01T00:15:00+00:00", "8.41"), _feat("2026-01-01T00:00:00+00:00", "8.50")]
    )
    assert list(df["value"]) == [8.50, 8.41]  # sorted by time, strings -> float
    assert str(df["time"].dt.tz) == "UTC"


def test_blank_and_nonnumeric_values_become_nan():
    df = features_to_frame(
        [_feat("2026-01-01T00:00:00+00:00", ""), _feat("2026-01-01T00:15:00+00:00", "Ssn")]
    )
    assert df["value"].isna().all()


def test_duplicates_keep_latest_revision():
    df = features_to_frame(
        [
            _feat("2026-01-01T00:00:00+00:00", "8.0", "Provisional"),
            _feat("2026-01-01T00:00:00+00:00", "7.9", "Approved"),
        ]
    )
    assert len(df) == 1
    assert df.loc[0, "approval_status"] == "Approved"


def test_approved_wins_even_when_listed_first():
    df = features_to_frame(
        [
            _feat("2026-01-01T00:00:00+00:00", "7.9", "Approved"),
            _feat("2026-01-01T00:00:00+00:00", "8.0", "Provisional"),
        ]
    )
    assert len(df) == 1
    assert df.loc[0, "approval_status"] == "Approved"
    assert df.loc[0, "value"] == 7.9


def test_empty_input_returns_empty_frame_with_columns():
    df = features_to_frame([])
    assert df.empty and "value" in df.columns
