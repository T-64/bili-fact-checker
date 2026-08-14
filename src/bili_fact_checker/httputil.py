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


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_STATUS
    if isinstance(exc, urllib.error.URLError):
        return True
    return False


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
                return resp.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts or not _is_retryable(exc):
                raise
            time.sleep(0.2 * (2 ** attempt))
    raise last_error or RuntimeError("request failed")


def get_json(
    url: str,
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> Any:
    raw = open_url(url, proxy=proxy, headers=headers, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 120,
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
    )
    return json.loads(raw.decode("utf-8"))


def quote(s: str) -> str:
    return urllib.parse.quote(s)
