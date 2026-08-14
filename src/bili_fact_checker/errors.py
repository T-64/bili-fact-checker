"""Typed application errors with stable codes for CLI, API, and UI."""

from __future__ import annotations


class BfcError(RuntimeError):
    code = "error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class LoginRequiredError(BfcError):
    code = "login_required"


class ProviderAuthError(BfcError):
    code = "provider_auth_failed"
