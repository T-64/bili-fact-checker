from __future__ import annotations

import email.message
import urllib.error

from bili_fact_checker.httputil import open_url


def _headers() -> email.message.Message:
    return email.message.Message()


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def open(self, _req, timeout=30):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_open_url_retries_retryable_http_errors(monkeypatch):
    opener = FakeOpener(
        [
            urllib.error.HTTPError("http://x", 503, "busy", hdrs=_headers(), fp=None),
            FakeResponse(b"ok"),
        ]
    )
    monkeypatch.setattr(
        "bili_fact_checker.httputil.urllib.request.build_opener",
        lambda *_args: opener,
    )
    monkeypatch.setattr("bili_fact_checker.httputil.time.sleep", lambda _s: None)

    assert open_url("http://example.test/a") == b"ok"
    assert opener.calls == 2


def test_open_url_does_not_retry_client_errors(monkeypatch):
    opener = FakeOpener(
        [urllib.error.HTTPError("http://x", 404, "missing", hdrs=_headers(), fp=None)]
    )
    monkeypatch.setattr(
        "bili_fact_checker.httputil.urllib.request.build_opener",
        lambda *_args: opener,
    )

    try:
        open_url("http://example.test/a")
        raise AssertionError("expected HTTP error")
    except RuntimeError as exc:
        assert "HTTP 404" in str(exc)
    assert opener.calls == 1


def test_open_url_does_not_retry_read_timeouts(monkeypatch):
    opener = FakeOpener([TimeoutError("The read operation timed out")])
    monkeypatch.setattr(
        "bili_fact_checker.httputil.urllib.request.build_opener",
        lambda *_args: opener,
    )

    try:
        open_url("http://example.test/a", retries=4, timeout=1)
        raise AssertionError("expected timeout")
    except TimeoutError:
        pass
    assert opener.calls == 1
