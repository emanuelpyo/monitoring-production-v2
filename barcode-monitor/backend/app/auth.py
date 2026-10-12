"""Authentication compatible with the FactoryPulse HMAC bearer-token convention.

Token format (three dot-separated base64url segments):

    <header>.<payload>.<signature>

where:
  * header  = base64url(JSON {"alg":"HS256","typ":"JWT"}) without padding
  * payload = base64url(JSON {"sub": username, "role": role, "exp": unix_ts})
  * signature = base64url(HMAC_SHA256(secret, f"{header}.{payload}")) without padding

The signature is verified with ``hmac.compare_digest`` (constant time) and the
``exp`` claim is enforced. This is a deliberately minimal local-development
scheme, not a production identity provider.

Security note: the HMAC ``AUTH_SECRET`` never reaches the browser. The frontend
exchanges a development username/password for a bearer token via
``POST /api/auth/login`` and reuses that token for REST and WebSocket calls.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Header, HTTPException

from .config import settings

# Development-only users. Passwords are intentionally simple because this is a
# local prototype; they live here rather than in the committed .env to keep the
# demo runnable out of the box. Override AUTH_SECRET in .env for real use.
DEV_USERS: dict[str, dict[str, str]] = {
    "viewer": {"password": "viewer123", "role": "viewer", "display_name": "Line Viewer"},
    "operator": {"password": "operator123", "role": "operator", "display_name": "Line Operator"},
    "admin": {"password": "admin123", "role": "admin", "display_name": "System Admin"},
}

# Roles allowed to change generator state / reset the dataset.
CONTROL_ROLES = {"operator", "admin"}


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(header: str, payload: str) -> str:
    message = f"{header}.{payload}".encode()
    return _b64encode(hmac.new(settings.auth_secret.encode(), message, hashlib.sha256).digest())


def create_token(username: str, role: str) -> str:
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64encode(
        json.dumps(
            {
                "sub": username,
                "role": role,
                "exp": int(time.time()) + settings.token_ttl_seconds,
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{header}.{payload}.{_sign(header, payload)}"


def verify_token(token: str) -> dict[str, Any]:
    try:
        header, payload, signature = token.split(".")
        expected = _sign(header, payload)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        claims = json.loads(_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    if int(claims.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Authentication token expired")
    if claims.get("sub") not in DEV_USERS:
        raise HTTPException(status_code=401, detail="Unknown user")
    return claims


def authenticate_user(username: str, password: str) -> dict[str, str]:
    user = DEV_USERS.get(username)
    if not user or not hmac.compare_digest(user["password"], password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"username": username, "role": user["role"], "display_name": user["display_name"]}


def user_from_header(authorization: str | None) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    claims = verify_token(authorization.removeprefix("Bearer ").strip())
    username = str(claims["sub"])
    user = DEV_USERS[username]
    return {"username": username, "role": user["role"], "display_name": user["display_name"]}


def require_control_role(user: dict[str, str]) -> None:
    if user["role"] not in CONTROL_ROLES:
        raise HTTPException(status_code=403, detail="This role cannot control the generator")


# Convenience FastAPI dependency-compatible helpers.
def current_user(authorization: str | None = Header(default=None)) -> dict[str, str]:
    """FastAPI dependency: resolve and validate the current user from the header."""
    return user_from_header(authorization)
