"""HTTP helpers with optional proxy and bounded retries."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bili_fact_checker.config import UA

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return True
        return "timed out" in str(reason or exc).lower()
    return False


def _is_retryable(exc: BaseException) -> bool:
    # A hung LLM/read timeout usually fails the same way again; retrying
    # 120s × N just turns one stall into a 10-minute job failure.
    if _is_timeout(exc):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_STATUS
    if isinstance(exc, urllib.error.URLError):
        return True
    return False


def _retry_delay(exc: BaseException, attempt: int) -> float:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return min(8.0, 1.0 * (2 ** attempt))
    return 0.2 * (2 ** attempt)


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    snippet = ""
    try:
        raw = exc.read()
        snippet = raw.decode("utf-8", "replace").strip().replace("\n", " ")[:240]
    except Exception:
        snippet = ""
    detail = f"HTTP {exc.code} {exc.reason}"
    return f"{detail}: {snippet}" if snippet else detail


def open_url_with_headers(
    url: str,
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = 30,
    retries: int = 2,
) -> tuple[dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    handlers: list[Any] = []
    if proxy:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    opener = urllib.request.build_opener(*handlers)
    last_error: BaseException | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw_headers = getattr(resp, "headers", None) or {}
                parsed_headers = {str(k): str(v) for k, v in raw_headers.items()}
                return parsed_headers, resp.read()
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(_http_error_message(exc))
            last_error.__cause__ = exc
            if attempt + 1 >= attempts or not _is_retryable(exc):
                raise last_error from exc
            time.sleep(_retry_delay(exc, attempt))
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts or not _is_retryable(exc):
                raise
            time.sleep(_retry_delay(exc, attempt))
    raise last_error or RuntimeError("request failed")


def open_url(
    url: str,
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = 30,
    retries: int = 2,
) -> bytes:
    _headers, body = open_url_with_headers(
        url,
        proxy=proxy,
        headers=headers,
        data=data,
        method=method,
        timeout=timeout,
        retries=retries,
    )
    return body


def get_json(
    url: str,
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    retries: int = 2,
) -> Any:
    raw = open_url(
        url, proxy=proxy, headers=headers, timeout=timeout, retries=retries
    )
    return json.loads(raw.decode("utf-8"))


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 120,
    retries: int = 2,
) -> Any:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    raw = open_url(
        url,
        proxy=proxy,
        headers=hdrs,
        data=body,
        method="POST",
        timeout=timeout,
        retries=retries,
    )
    return json.loads(raw.decode("utf-8"))


def quote(s: str) -> str:
    return urllib.parse.quote(s)
