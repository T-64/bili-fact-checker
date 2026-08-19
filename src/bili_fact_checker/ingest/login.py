"""Official Bilibili QR login: capture SESSDATA for subtitle access."""

from __future__ import annotations

import http.cookiejar
import io
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bili_fact_checker.config import (
    Settings,
    UA,
    load_user_config,
    save_user_config,
    user_config_path,
)
from bili_fact_checker.httputil import quote

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
PASSPORT_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

POLL_PENDING = 86101
POLL_SCANNED = 86090
POLL_EXPIRED = 86038
POLL_SUCCESS = 0
COOKIE_NAMES = (
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "sid",
    "buvid3",
    "buvid4",
)


@dataclass(frozen=True)
class LoginQr:
    qrcode_key: str
    url: str


@dataclass(frozen=True)
class LoginPoll:
    status: str
    message: str = ""
    cookies: dict[str, str] | None = None


@dataclass(frozen=True)
class PassportResponse:
    body: dict[str, Any]
    cookies: dict[str, str]


def _opener(proxy: str, cookie_jar: http.cookiejar.CookieJar | None):
    handlers: list[Any] = []
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    if proxy:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return urllib.request.build_opener(*handlers)


def _passport_request(
    settings: Settings, url: str, *, capture_cookies: bool = False
) -> PassportResponse:
    jar = http.cookiejar.CookieJar() if capture_cookies else None
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    for key, value in PASSPORT_HEADERS.items():
        req.add_header(key, value)
    opener = _opener(settings.proxy, jar)
    with opener.open(req, timeout=8) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    cookies = {cookie.name: cookie.value for cookie in jar} if jar else {}
    return PassportResponse(body=body if isinstance(body, dict) else {}, cookies=cookies)


def qr_svg(data: str) -> str:
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    buf = io.BytesIO()
    image.save(buf)
    return buf.getvalue().decode("utf-8")


def print_qr_ascii(data: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def generate_login_qr(settings: Settings) -> LoginQr:
    response = _passport_request(settings, GENERATE_URL)
    if response.body.get("code") not in (0, None):
        raise RuntimeError(
            f"获取登录二维码失败：{response.body.get('message') or response.body.get('code')}"
        )
    payload = response.body.get("data") or {}
    key = str(payload.get("qrcode_key") or "").strip()
    url = str(payload.get("url") or "").strip()
    if not key or not url:
        raise RuntimeError("获取登录二维码失败：返回缺少 qrcode_key")
    return LoginQr(qrcode_key=key, url=url)


def _cookies_from_success(jar_cookies: dict[str, str], data_url: str) -> dict[str, str]:
    cookies = {
        name: value
        for name, value in jar_cookies.items()
        if name in COOKIE_NAMES and value
    }
    if data_url:
        query = parse_qs(urlsplit(data_url).query)
        for name in COOKIE_NAMES:
            values = query.get(name) or []
            if values and name not in cookies:
                cookies[name] = values[0]
    return cookies


def poll_login_qr(settings: Settings, qrcode_key: str) -> LoginPoll:
    key = qrcode_key.strip()
    if not key:
        raise ValueError("qrcode_key is required")
    url = f"{POLL_URL}?qrcode_key={quote(key)}&source=main-fe-header"
    try:
        response = _passport_request(settings, url, capture_cookies=True)
    except TimeoutError:
        return LoginPoll(status="pending", message="登录接口超时，继续等待")
    except urllib.error.URLError:
        return LoginPoll(status="pending", message="登录接口暂时不可达，继续等待")
    if response.body.get("code") not in (0, None):
        return LoginPoll(
            status="error",
            message=str(response.body.get("message") or "登录接口返回错误"),
        )
    payload = response.body.get("data") or {}
    code = int(payload.get("code") or 0)
    message = str(payload.get("message") or "")
    if code == POLL_PENDING:
        return LoginPoll(status="pending", message=message or "未扫码")
    if code == POLL_SCANNED:
        return LoginPoll(status="scanned", message=message or "已扫码，等待确认")
    if code == POLL_EXPIRED:
        return LoginPoll(status="expired", message=message or "二维码已过期")
    if code != POLL_SUCCESS:
        return LoginPoll(status="error", message=message or f"登录失败（{code}）")
    cookies = _cookies_from_success(
        response.cookies, str(payload.get("url") or "")
    )
    data_url = str(payload.get("url") or "")
    if not cookies.get("SESSDATA") and data_url:
        try:
            followed = _passport_request(settings, data_url, capture_cookies=True)
        except (TimeoutError, urllib.error.URLError):
            followed = PassportResponse(body={}, cookies={})
        cookies = _cookies_from_success(
            {**cookies, **followed.cookies}, data_url
        )
    if not cookies.get("SESSDATA"):
        return LoginPoll(status="error", message="登录成功但未返回 SESSDATA")
    return LoginPoll(status="success", message="登录成功", cookies=cookies)


def parse_netscape_cookies(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#HttpOnly_"):
            raw = raw[len("#HttpOnly_") :]
        elif raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 7:
            continue
        domain, name, value = parts[0], parts[5], parts[6]
        if "bilibili.com" not in domain.lower() or not name or not value:
            continue
        cookies[name] = value
    return cookies


def cookie_header(settings: Settings) -> str:
    path = settings.cookie_file
    if path.is_file():
        try:
            cookies = parse_netscape_cookies(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            cookies = {}
        if cookies:
            return "; ".join(f"{name}={value}" for name, value in cookies.items())
    if settings.sessdata:
        return f"SESSDATA={settings.sessdata}"
    return ""


def import_netscape_cookie_text(
    settings: Settings,
    text: str,
    *,
    persist_config: bool = False,
    sessdata_path: Path | None = None,
    cookie_file: Path | None = None,
) -> Settings:
    cookies = parse_netscape_cookies(text)
    if not cookies.get("SESSDATA"):
        raise RuntimeError("cookies.txt 里没有 .bilibili.com 的 SESSDATA")
    sess_path = sessdata_path or default_sessdata_path()
    jar_path = cookie_file or settings.cookie_file
    payload = text if text.endswith("\n") else text + "\n"
    sess_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    jar_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    sess_path.write_text(cookies["SESSDATA"] + "\n", encoding="utf-8")
    sess_path.chmod(0o600)
    jar_path.write_text(payload, encoding="utf-8")
    jar_path.chmod(0o600)
    if persist_config:
        saved = load_user_config()
        save_user_config(
            {
                "openai_api_base": saved.get("openai_api_base")
                or settings.openai_api_base,
                "openai_api_key": saved.get("openai_api_key")
                or settings.openai_api_key,
                "openai_model": saved.get("openai_model") or settings.openai_model,
                "sessdata": cookies["SESSDATA"],
            }
        )
    return replace(settings, sessdata=cookies["SESSDATA"])


def default_sessdata_path() -> Path:
    return Path.home() / ".config" / "bili" / "SESSDATA"


def write_netscape_cookies(path: Path, cookies: dict[str, str]) -> None:
    expires = int(time.time()) + 180 * 24 * 3600
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by bili-fact-checker. Do not share this file.",
        "",
    ]
    for name, value in cookies.items():
        if not name or not value:
            continue
        secure = "TRUE" if name == "SESSDATA" else "FALSE"
        lines.append(f".bilibili.com\tTRUE\t/\t{secure}\t{expires}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def persist_bili_login(
    settings: Settings,
    cookies: dict[str, str],
    *,
    persist_config: bool = False,
    sessdata_path: Path | None = None,
    cookie_file: Path | None = None,
) -> Settings:
    sessdata = (cookies.get("SESSDATA") or "").strip()
    if not sessdata:
        raise RuntimeError("没有可保存的 SESSDATA")
    sess_path = sessdata_path or default_sessdata_path()
    jar_path = cookie_file or settings.cookie_file
    sess_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    sess_path.write_text(sessdata + "\n", encoding="utf-8")
    sess_path.chmod(0o600)
    write_netscape_cookies(jar_path, cookies)
    if persist_config:
        saved = load_user_config()
        save_user_config(
            {
                "openai_api_base": saved.get("openai_api_base")
                or settings.openai_api_base,
                "openai_api_key": saved.get("openai_api_key")
                or settings.openai_api_key,
                "openai_model": saved.get("openai_model") or settings.openai_model,
                "sessdata": sessdata,
            }
        )
    return replace(settings, sessdata=sessdata)


def should_update_user_config(persist: bool) -> bool:
    return persist or user_config_path().is_file()


class QrLoginMonitor:
    """Poll Bilibili on the server so a page reload does not drop the login."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._state: dict[str, str] = {
            "status": "idle",
            "message": "",
            "url": "",
            "svg": "",
            "uname": "",
        }

    def public_state(self) -> dict[str, str]:
        with self._lock:
            return dict(self._state)

    def start(
        self,
        settings: Settings,
        qr: LoginQr,
        svg: str,
        persist_hook: Callable[[dict[str, str]], tuple[bool, str]],
    ) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._state = {
                "status": "pending",
                "message": "等待扫码确认",
                "url": qr.url,
                "svg": svg,
                "uname": "",
            }
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(settings, qr.qrcode_key, persist_hook, generation),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _set(self, **fields: str) -> None:
        with self._lock:
            self._state.update(fields)

    def _run(
        self,
        settings: Settings,
        qrcode_key: str,
        persist_hook: Callable[[dict[str, str]], tuple[bool, str]],
        generation: int,
    ) -> None:
        while True:
            if generation != self._generation:
                return
            try:
                result = poll_login_qr(settings, qrcode_key)
            except Exception as exc:
                self._set(status="error", message=f"查询登录状态失败：{exc}")
                return
            if generation != self._generation:
                return
            if result.status == "pending":
                self._set(status="pending", message=result.message or "等待扫码确认")
            elif result.status == "scanned":
                self._set(status="scanned", message=result.message or "已扫码，请在手机上确认")
            elif result.status == "success":
                try:
                    ok, info = persist_hook(result.cookies or {})
                except Exception as exc:
                    self._set(status="error", message=f"保存登录态失败：{exc}")
                    return
                if ok:
                    self._set(
                        status="success",
                        message=f"已登录 {info}",
                        uname=info,
                        svg="",
                    )
                else:
                    self._set(
                        status="error",
                        message=f"Cookie 已写入，但登录校验失败：{info}",
                        svg="",
                    )
                return
            else:
                self._set(status=result.status, message=result.message)
                return
            if self._stop.wait(1.2):
                return


def sync_sessdata_from_cookie_file(settings: Settings) -> Settings:
    if not settings.cookie_file.is_file():
        return settings
    try:
        cookies = parse_netscape_cookies(
            settings.cookie_file.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return settings
    sess = cookies.get("SESSDATA") or ""
    if not sess or sess == settings.sessdata:
        return settings
    return replace(settings, sessdata=sess)


def public_login_state(settings: Settings) -> dict[str, object]:
    settings = sync_sessdata_from_cookie_file(settings)
    if not cookie_header(settings):
        return {"logged_in": False, "name": "", "message": "未登录"}
    from bili_fact_checker.ingest import check_bili_login

    ok, info = check_bili_login(settings)
    if ok:
        return {"logged_in": True, "name": info, "message": f"已登录 {info}"}
    return {"logged_in": False, "name": "", "message": info}
