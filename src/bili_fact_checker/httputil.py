"""HTTP helpers with optional proxy."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bili_fact_checker.config import UA


def open_url(
    url: str,
    *,
    proxy: str = "",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = 30,
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
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def get_json(url: str, *, proxy: str = "", headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
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
    raw = open_url(url, proxy=proxy, headers=hdrs, data=body, method="POST", timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def quote(s: str) -> str:
    return urllib.parse.quote(s)
