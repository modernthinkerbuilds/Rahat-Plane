"""Shared-secret bearer-token auth for the localhost adapter surface.

The adapter listens on 127.0.0.1 by default, but adding a shared-secret
header is a low-cost belt-and-suspenders against a curious browser tab or
loopback-misbehaving process. The secret lives in ``OPENCLAW_ADAPTER_TOKEN``
in `.env`. The OpenClaw plugin reads the same env and includes it on every
request.
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, status

_TOKEN_ENV = "OPENCLAW_ADAPTER_TOKEN"


def get_expected_token() -> str:
    return os.environ.get(_TOKEN_ENV, "").strip()


def require_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Reject any request whose ``Authorization: Bearer <token>`` header
    doesn't match the configured shared secret.

    If ``OPENCLAW_ADAPTER_TOKEN`` is unset (dev mode), auth is bypassed
    so the user can curl the surface during Stage 0 hello-world. Set the
    env once OpenClaw is reachable end-to-end.
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
