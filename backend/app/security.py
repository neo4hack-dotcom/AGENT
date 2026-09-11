"""Who may reach the admin area.

Two modes, and which one is active is always visible in the UI rather than implied:

* **no password set** — admin is reachable from this machine only. That is the right
  default for a personal agent on a laptop: no credential to invent before you can
  configure anything, and nothing exposed if the port is ever forwarded.
* **password set** — required from everywhere, loopback included, in exchange for a
  bearer token. Failed attempts back off, because a four-character password on a LAN
  otherwise falls in seconds.
"""

from __future__ import annotations

import hmac
import secrets
import time

from fastapi import HTTPException, Request

from app.store import new_id, now

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW_S = 300


def is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in LOOPBACK


def mode(container) -> str:
    return "password" if (container.env.admin_password or "").strip() else "local-only"


def _prune(key: str) -> list[float]:
    cutoff = time.time() - ATTEMPT_WINDOW_S
    kept = [t for t in _ATTEMPTS.get(key, []) if t > cutoff]
    _ATTEMPTS[key] = kept
    return kept


def login(container, request: Request, password: str) -> dict:
    expected = (container.env.admin_password or "").strip()
    if not expected:
        raise HTTPException(400, "No admin password is set — admin is open from this machine.")
    key = (request.client.host if request.client else "unknown") or "unknown"
    if len(_prune(key)) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    if not hmac.compare_digest(password or "", expected):
        _ATTEMPTS.setdefault(key, []).append(time.time())
        raise HTTPException(401, "Wrong password.")
    _ATTEMPTS.pop(key, None)
    token = secrets.token_urlsafe(32)
    container.store.sessions()[token] = {"id": new_id("s"), "created_at": now(),
                                         "expires_at": now() + container.env.session_ttl_s}
    container.store.touch()
    return {"token": token, "expires_at": now() + container.env.session_ttl_s}


def logout(container, token: str) -> None:
    if container.store.sessions().pop(token, None) is not None:
        container.store.touch()


def _valid_token(container, token: str) -> bool:
    session = container.store.sessions().get(token)
    if session is None:
        return False
    if session.get("expires_at", 0) < now():
        container.store.sessions().pop(token, None)
        container.store.touch()
        return False
    return True


def check_admin(container, request: Request) -> None:
    """Raise unless this request may touch the admin area."""
    if mode(container) == "local-only":
        if is_local(request):
            return
        raise HTTPException(
            403,
            "Admin is restricted to the machine Lumen runs on. To administer it from "
            "elsewhere, set LUMEN_ADMIN_PASSWORD and restart.")
    header = request.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token or not _valid_token(container, token):
        raise HTTPException(401, "Admin sign-in required.")


def admin_state(container, request: Request) -> dict:
    """What the browser needs to decide whether to show the admin door at all."""
    current = mode(container)
    if current == "local-only":
        return {"mode": current, "authenticated": is_local(request), "local": is_local(request)}
    header = request.headers.get("authorization") or ""
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return {"mode": current, "authenticated": bool(token and _valid_token(container, token)),
            "local": is_local(request)}
