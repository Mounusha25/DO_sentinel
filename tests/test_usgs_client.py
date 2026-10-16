"""Client tests against a fake session; no network. Responses are hand-written."""
import pytest
import requests

from dosentinel.ingest import UsgsClient


class _Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


def _client(responses, **kw):
    sess = _Session(responses)
    return UsgsClient(session=sess, backoff=0.0, **kw), sess


def test_follows_next_links_and_drops_params_on_followup():
    page1 = {"features": [{"id": 1}], "links": [{"rel": "next", "href": "https://x/next"}]}
    page2 = {"features": [{"id": 2}], "links": []}
    client, sess = _client([_Resp(payload=page1), _Resp(payload=page2)])
    feats = list(client.iter_features("continuous", {"limit": 1}))
    assert [f["id"] for f in feats] == [1, 2]
    assert sess.calls[0]["params"]["limit"] == 1
    assert sess.calls[1]["url"] == "https://x/next"
    assert sess.calls[1]["params"] is None


def test_retries_transient_status_then_succeeds():
    client, sess = _client([_Resp(429), _Resp(503), _Resp(payload={"features": []})])
    assert list(client.iter_features("continuous", {})) == []
    assert len(sess.calls) == 3


def test_429_honors_retry_after_and_caps_it(monkeypatch):
    sleeps = []
    monkeypatch.setattr("dosentinel.ingest.usgs.time.sleep", sleeps.append)
    client, _ = _client(
        [
            _Resp(429, headers={"Retry-After": "581"}),
            _Resp(429, headers={"Retry-After": "99999"}),
            _Resp(429, headers={"Retry-After": "soon"}),
            _Resp(payload={"features": []}),
        ],
        max_retry_after=600.0,
    )
    assert list(client.iter_features("continuous", {})) == []
    assert sleeps == [581.0, 600.0, 0.0]


def test_gives_up_after_max_retries():
    client, sess = _client([_Resp(500)] * 3, max_retries=3)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        list(client.iter_features("continuous", {}))
    assert len(sess.calls) == 3


def test_non_retryable_error_raises_immediately():
    client, sess = _client([_Resp(404)])
    with pytest.raises(requests.HTTPError):
        list(client.iter_features("continuous", {}))
    assert len(sess.calls) == 1


def test_api_key_sent_as_header(monkeypatch):
    monkeypatch.delenv("USGS_API_KEY", raising=False)
    sess = _Session([_Resp(payload={"features": []})])
    UsgsClient(api_key="k", session=sess).fetch_continuous("USGS-1", "00300", "a", "b")
    assert sess.calls[0]["headers"]["X-Api-Key"] == "k"
    assert sess.calls[0]["params"]["time"] == "a/b"


def test_key_with_a_trailing_newline_or_spaces_is_cleaned(monkeypatch):
    # a secret pasted into a form often ends in a newline, which is an illegal HTTP header value
    monkeypatch.setenv("USGS_API_KEY", "  abc123\n")
    client, sess = _client([_Resp(payload={"features": []})])
    list(client.iter_features("continuous", {}))
    assert sess.calls[0]["headers"]["X-Api-Key"] == "abc123"
    monkeypatch.setenv("USGS_API_KEY", "\n")
    assert UsgsClient(session=_Session([])).api_key is None  # a blank secret means "no key", not a bad header
