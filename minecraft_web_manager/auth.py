"""Small dependency-free HMAC token implementation for the local dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(secret: str, username: str, ttl_seconds: int) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl_seconds
    header = _encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = _encode(json.dumps({"sub": username, "role": "admin", "exp": expires_at}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}", expires_at


def verify_token(secret: str, token: str) -> dict[str, Any] | None:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}".encode()
        expected = _encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return None
        claims = json.loads(_decode(payload))
        if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
            return None
        return claims
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
