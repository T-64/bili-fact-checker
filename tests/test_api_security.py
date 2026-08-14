from __future__ import annotations

import argparse

import pytest

from bili_fact_checker.cli import cmd_serve
from bili_fact_checker.config import is_loopback_host, validate_api_bind


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.1.2.3", "::1", "[::1]", "localhost", "LOCALHOST."],
)
def test_loopback_bind_allows_empty_api_token(host):
    assert is_loopback_host(host)
    validate_api_bind(host, "")


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "api.example", "localhost.example"],
)
def test_non_loopback_bind_requires_strong_api_token(host):
    assert not is_loopback_host(host)
    with pytest.raises(ValueError, match="refusing non-loopback API bind"):
        validate_api_bind(host, "")
    with pytest.raises(ValueError, match="at least 32 characters"):
        validate_api_bind(host, "short-token")

    validate_api_bind(host, "K9x_yR4m2Vq8pL7n5Tz3cW6j1Hs0DfUa")


def test_public_token_length_boundary_and_whitespace():
    with pytest.raises(ValueError, match="at least 32 characters"):
        validate_api_bind("0.0.0.0", "x" * 31)
    with pytest.raises(ValueError, match="at least 32 characters"):
        validate_api_bind("0.0.0.0", " " * 32)
    validate_api_bind("0.0.0.0", "x" * 32)


def test_serve_refuses_public_bind_before_starting_uvicorn(monkeypatch):
    monkeypatch.delenv("BFC_API_TOKEN", raising=False)

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("uvicorn must not start")

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", unexpected_run)
    with pytest.raises(ValueError, match="BFC_API_TOKEN"):
        cmd_serve(argparse.Namespace(host="0.0.0.0", port=8765))


def test_serve_starts_public_bind_with_strong_token(monkeypatch):
    monkeypatch.setenv("BFC_API_TOKEN", "K9x_yR4m2Vq8pL7n5Tz3cW6j1Hs0DfUa")
    observed = {}

    def fake_run(app, **kwargs):
        observed.update(app=app, **kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert cmd_serve(argparse.Namespace(host="0.0.0.0", port=9876)) == 0
    assert observed["host"] == "0.0.0.0"
    assert observed["port"] == 9876
