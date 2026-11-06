"""Shared-secret bearer-token auth for the localhost adapter surface.

The adapter listens on 127.0.0.1 by default, but adding a shared-secret
header is a low-cost belt-and-suspenders against a curious browser tab or
loopback-misbehaving process. The secret lives in ``RAHAT_ADAPTER_TOKEN``
in `.env`; every caller (the runner's HTTP client) sends it as a bearer
token.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from new_plane.env import setting

_TOKEN_ENV = "RAHAT_ADAPTER_TOKEN"


def get_expected_token() -> str:
    return (setting(_TOKEN_ENV) or "").strip()


def require_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Reject any request whose ``Authorization: Bearer <token>`` header
    doesn't match the configured shared secret.

    If ``RAHAT_ADAPTER_TOKEN`` is unset (dev mode), auth is bypassed
    so the surface can be curled locally. Set it for anything that runs
    unattended.
    """
    expected = get_expected_token()
    if not expected:
        return  # dev mode — adapter binds to localhost; auth disabled
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if presented != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid adapter token",
        )
