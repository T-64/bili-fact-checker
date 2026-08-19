from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from bili_fact_checker.cli import build_parser, cmd_login
from bili_fact_checker.config import Settings, load_user_config, save_user_config
from bili_fact_checker.ingest.login import (
    LoginPoll,
    LoginQr,
    PassportResponse,
    QrLoginMonitor,
    generate_login_qr,
    import_netscape_cookie_text,
    parse_netscape_cookies,
    persist_bili_login,
    poll_login_qr,
    write_netscape_cookies,
)
from tests.test_setup import isolate_config


def test_generate_login_qr_reads_key_and_url(monkeypatch):
    monkeypatch.setattr(
        "bili_fact_checker.ingest.login._passport_request",
        lambda *_a, **_k: PassportResponse(
            body={
                "code": 0,
                "data": {
                    "url": "https://account.bilibili.com/h5/scan?qrcode_key=abc",
                    "qrcode_key": "abc12345def",
                },
            },
            cookies={},
        ),
    )
    qr = generate_login_qr(Settings.from_env())
    assert qr.qrcode_key == "abc12345def"
    assert qr.url.startswith("https://account.bilibili.com/")


def test_poll_maps_pending_scanned_expired_and_success(monkeypatch):
    states = [
        PassportResponse(
            body={"code": 0, "data": {"code": 86101, "message": "未扫码"}},
            cookies={},
        ),
        PassportResponse(
            body={"code": 0, "data": {"code": 86090, "message": "已扫码"}},
            cookies={},
        ),
        PassportResponse(
            body={"code": 0, "data": {"code": 86038, "message": "过期"}},
            cookies={},
        ),
        PassportResponse(
            body={
                "code": 0,
                "data": {
                    "code": 0,
                    "url": "https://passport.bilibili.com/x/cross?SESSDATA=from-url",
                },
            },
            cookies={"SESSDATA": "from-cookie", "bili_jct": "jct"},
        ),
    ]

    def fake_request(*_a, **_k):
        return states.pop(0)

    monkeypatch.setattr(
        "bili_fact_checker.ingest.login._passport_request", fake_request
    )
    settings = Settings.from_env()
    assert poll_login_qr(settings, "abcdefgh").status == "pending"
    assert poll_login_qr(settings, "abcdefgh").status == "scanned"
    assert poll_login_qr(settings, "abcdefgh").status == "expired"
    success = poll_login_qr(settings, "abcdefgh")
    assert success.status == "success"
    assert success.cookies["SESSDATA"] == "from-cookie"
    assert success.cookies["bili_jct"] == "jct"


def test_poll_falls_back_to_redirect_url_cookies(monkeypatch):
    monkeypatch.setattr(
        "bili_fact_checker.ingest.login._passport_request",
        lambda *_a, **_k: PassportResponse(
            body={
                "code": 0,
                "data": {
                    "code": 0,
                    "url": "https://example.test/x?SESSDATA=url-token&DedeUserID=9",
                },
            },
            cookies={},
        ),
    )
    result = poll_login_qr(Settings.from_env(), "abcdefgh")
    assert result.status == "success"
    assert result.cookies["SESSDATA"] == "url-token"
    assert result.cookies["DedeUserID"] == "9"


def test_poll_timeout_stays_pending(monkeypatch):
    def boom(*_a, **_k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "bili_fact_checker.ingest.login._passport_request", boom
    )
    result = poll_login_qr(Settings.from_env(), "abcdefgh")
    assert result.status == "pending"
    assert "继续等待" in result.message


def test_qr_monitor_saves_login_without_frontend_poll(monkeypatch):
    monkeypatch.setattr(
        "bili_fact_checker.ingest.login.poll_login_qr",
        lambda *_a, **_k: LoginPoll(
            status="success", cookies={"SESSDATA": "monitor-secret"}
        ),
    )
    saved: list[dict[str, str]] = []

    def persist(cookies: dict[str, str]) -> tuple[bool, str]:
        saved.append(cookies)
        return True, "网页用户"

    monitor = QrLoginMonitor()
    monitor.start(
        Settings.from_env(),
        LoginQr(qrcode_key="abcdefghij", url="https://example.test/qr"),
        "<svg></svg>",
        persist,
    )
    try:
        state = {}
        for _ in range(50):
            state = monitor.public_state()
            if state["status"] == "success":
                break
            time.sleep(0.05)
        assert state["status"] == "success"
        assert state["uname"] == "网页用户"
        assert "monitor-secret" not in json.dumps(state)
        assert saved and saved[0]["SESSDATA"] == "monitor-secret"
    finally:
        monitor.stop()


def test_persist_writes_sessdata_and_netscape(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    settings = replace(
        Settings.from_env(), cookie_file=tmp_path / "cookies.txt"
    )
    sess = tmp_path / "SESSDATA"
    merged = persist_bili_login(
        settings,
        {"SESSDATA": "tok%2Cvalue", "bili_jct": "jct", "DedeUserID": "1"},
        persist_config=False,
        sessdata_path=sess,
    )
    assert merged.sessdata == "tok%2Cvalue"
    assert sess.read_text(encoding="utf-8").strip() == "tok%2Cvalue"
    cookie_text = (tmp_path / "cookies.txt").read_text(encoding="utf-8")
    assert "SESSDATA" in cookie_text
    assert "tok%2Cvalue" in cookie_text
    assert "bili_jct" in cookie_text


def test_public_login_state_without_cookie_does_not_claim_login():
    from bili_fact_checker.ingest.login import public_login_state

    state = public_login_state(
        replace(
            Settings.from_env(),
            sessdata="",
            cookie_file=Path("/no/such/bili-cookies.txt"),
        )
    )
    assert state["logged_in"] is False
    assert state["name"] == ""


def test_persist_merges_sessdata_into_existing_config(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    save_user_config(
        {
            "openai_api_base": "https://api.example.test/v1",
            "openai_api_key": "keep-me",
            "openai_model": "keep-model",
            "sessdata": "expired",
        }
    )
    settings = replace(
        Settings.from_env(), cookie_file=tmp_path / "cookies.txt"
    )
    persist_bili_login(
        settings,
        {"SESSDATA": "fresh-token"},
        persist_config=True,
        sessdata_path=tmp_path / "SESSDATA",
    )
    saved = load_user_config()
    assert saved["sessdata"] == "fresh-token"
    assert saved["openai_api_key"] == "keep-me"
    assert saved["openai_model"] == "keep-model"


def test_netscape_cookie_file_is_private(tmp_path):
    path = tmp_path / "cookies.txt"
    write_netscape_cookies(path, {"SESSDATA": "secret", "bili_jct": "jct"})
    assert path.stat().st_mode & 0o777 == 0o600


def test_cmd_login_saves_without_printing_cookie(
    tmp_path, monkeypatch, capsys
):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "bili_fact_checker.cli.generate_login_qr",
        lambda _s: LoginQr(
            qrcode_key="abcdefghij",
            url="https://account.bilibili.com/h5/scan",
        ),
    )
    monkeypatch.setattr("bili_fact_checker.cli.print_qr_ascii", lambda _url: None)
    monkeypatch.setattr(
        "bili_fact_checker.cli.poll_login_qr",
        lambda *_a, **_k: LoginPoll(
            status="success",
            cookies={"SESSDATA": "super-secret-cookie"},
        ),
    )
    monkeypatch.setattr(
        "bili_fact_checker.cli.persist_bili_login",
        lambda settings, cookies, **_k: replace(
            settings, sessdata=cookies["SESSDATA"]
        ),
    )
    monkeypatch.setattr(
        "bili_fact_checker.cli.check_bili_login",
        lambda _s: (True, "测试用户"),
    )
    monkeypatch.setattr(
        "bili_fact_checker.cli.default_sessdata_path",
        lambda: tmp_path / "SESSDATA",
    )
    args = build_parser().parse_args(["login"])
    assert cmd_login(args) == 0
    output = capsys.readouterr().out
    assert "测试用户" in output
    assert "super-secret-cookie" not in output


def test_setup_bilibili_qr_endpoints_do_not_echo_cookie(tmp_path, monkeypatch):
    from bili_fact_checker.api.app import create_app

    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "bili_fact_checker.api.app.generate_login_qr",
        lambda _s: LoginQr(
            qrcode_key="abcdefghij",
            url="https://account.bilibili.com/h5/scan",
        ),
    )
    monkeypatch.setattr(
        "bili_fact_checker.api.app.qr_svg", lambda _url: "<svg></svg>"
    )
    monkeypatch.setattr(
        "bili_fact_checker.api.app.poll_login_qr",
        lambda *_a, **_k: LoginPoll(
            status="success", cookies={"SESSDATA": "api-secret-cookie"}
        ),
    )
    monkeypatch.setattr(
        "bili_fact_checker.ingest.login.poll_login_qr",
        lambda *_a, **_k: LoginPoll(
            status="success", cookies={"SESSDATA": "api-secret-cookie"}
        ),
    )
    monkeypatch.setattr(
        "bili_fact_checker.api.app.persist_bili_login",
        lambda settings, cookies, **_k: replace(
            settings, sessdata=cookies["SESSDATA"]
        ),
    )
    monkeypatch.setattr(
        "bili_fact_checker.api.app.check_bili_login",
        lambda _s: (True, "网页用户"),
    )
    app = create_app(replace(Settings.from_env(), data_dir=tmp_path, api_token=""))

    import asyncio

    import httpx

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            qr = await client.post("/v1/setup/bilibili/qrcode")
            assert qr.status_code == 200
            body = qr.json()
            assert body["qrcode_key"] == "abcdefghij"
            assert body["svg"] == "<svg></svg>"
            poll = await client.post(
                "/v1/setup/bilibili/poll",
                json={"qrcode_key": "abcdefghij", "persist": True},
            )
            assert poll.status_code == 200
            payload = poll.json()
            assert payload["status"] == "success"
            assert payload["uname"] == "网页用户"
            dumped = json.dumps(payload)
            assert "api-secret-cookie" not in dumped
            assert app.state.settings.sessdata == "api-secret-cookie"
            session = await client.get("/v1/setup/bilibili/session")
            assert session.status_code == 200
            body = session.json()
            assert "api-secret-cookie" not in json.dumps(body)
            assert body["status"] in {"pending", "scanned", "success", "error"}

    asyncio.run(scenario())


NETSCAPE_FIXTURE = """# Netscape HTTP Cookie File

.bilibili.com	TRUE	/	TRUE	1999999999	SESSDATA	fixture-sess
.bilibili.com	TRUE	/	FALSE	1999999999	bili_jct	fixture-jct
.bilibili.com	TRUE	/	FALSE	1999999999	DedeUserID	123
"""


def test_parse_netscape_keeps_bilibili_login_cookies():
    cookies = parse_netscape_cookies(NETSCAPE_FIXTURE)
    assert cookies["SESSDATA"] == "fixture-sess"
    assert cookies["bili_jct"] == "fixture-jct"
    assert cookies["DedeUserID"] == "123"


def test_import_netscape_writes_original_file(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    settings = replace(
        Settings.from_env(), cookie_file=tmp_path / "cookies.txt"
    )
    merged = import_netscape_cookie_text(
        settings,
        NETSCAPE_FIXTURE,
        persist_config=False,
        sessdata_path=tmp_path / "SESSDATA",
    )
    assert merged.sessdata == "fixture-sess"
    saved = (tmp_path / "cookies.txt").read_text(encoding="utf-8")
    assert "fixture-jct" in saved
    assert "DedeUserID" in saved


def test_cmd_login_from_file_does_not_print_cookie(
    tmp_path, monkeypatch, capsys
):
    isolate_config(monkeypatch, tmp_path)
    path = tmp_path / "bilibili_cookies.txt"
    path.write_text(NETSCAPE_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(
        "bili_fact_checker.cli.import_netscape_cookie_text",
        lambda settings, text, **_k: replace(settings, sessdata="fixture-sess"),
    )
    monkeypatch.setattr(
        "bili_fact_checker.cli.check_bili_login",
        lambda _s: (True, "文件用户"),
    )
    args = build_parser().parse_args(["login", "--from-file", str(path)])
    assert cmd_login(args) == 0
    output = capsys.readouterr().out
    assert "文件用户" in output
    assert "fixture-sess" not in output


def test_public_login_state_uses_cookie_file_without_sessdata_field(
    tmp_path, monkeypatch
):
    from bili_fact_checker.ingest.login import public_login_state

    jar = tmp_path / "cookies.txt"
    jar.write_text(NETSCAPE_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(
        "bili_fact_checker.ingest.check_bili_login",
        lambda _s: (True, "文件用户"),
    )
    state = public_login_state(
        replace(Settings.from_env(), sessdata="", cookie_file=jar)
    )
    assert state["logged_in"] is True
    assert state["name"] == "文件用户"

