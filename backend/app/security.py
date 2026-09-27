"""Who may reach the app, and who may reach the admin area.

The admin area has two modes, and which one is active is always visible in the UI:

* **no password set** — admin is reachable from this machine only. That is the right
  default for a personal agent on a laptop: no credential to invent before you can
  configure anything, and nothing exposed if the port is ever forwarded.
* **password set** — required from everywhere, loopback included, in exchange for a
  bearer token. Failed attempts back off, because a four-character password on a LAN
  otherwise falls in seconds.

The rest of the API answers this machine freely and everyone else only after sign-in —
with the access password (chat) or the admin password (chat and admin). With neither set,
nothing is served beyond this machine at all.

"This machine" is decided with care, because an agent that can run code and query the
bank's databases is worth attacking from a browser tab:

* a proxy in front (the Vite dev server) makes every client look like 127.0.0.1, so the
  forwarded-for chain must be loopback too — a proxy can make a request less local,
  never more;
* a web page on any site can make the browser send requests to localhost. Those are
  refused by origin (`Sec-Fetch-Site`, `Origin`), and DNS rebinding — a hostile domain
  that re-resolves to 127.0.0.1 to look same-origin — by the Host header, which must name
  this machine or a host the deployment declared.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from app.store import new_id, now

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
COOKIE = "agent_session"
_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW_S = 300


def _loopback(address: str) -> bool:
    address = (address or "").strip().strip("[]")
    return address in LOOPBACK or address.startswith("127.") or address == "::ffff:127.0.0.1"


def is_local(request: Request) -> bool:
    """The request comes from this machine — and so does everyone a proxy relayed it for."""
    host = (request.client.host if request.client else "") or ""
    if not _loopback(host):
        return False
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    return all(_loopback(hop) for hop in forwarded.split(",") if hop.strip())


def mode(container) -> str:
    return "password" if (container.env.admin_password or "").strip() else "local-only"


def remote_mode(container) -> str:
    """How someone on another machine gets in: 'password', or 'closed'."""
    env = container.env
    return "password" if ((env.admin_password or "").strip() or (env.access_password or "").strip()) else "closed"


# ------------------------------------------------------------ request origin

_HOST_PORT = re.compile(r"^(\[[^\]]+\]|[^:]+)(?::\d+)?$")


def _hostname(value: str) -> str:
    match = _HOST_PORT.match((value or "").strip().lower())
    return match.group(1).strip("[]") if match else ""


def allowed_hosts(container) -> list[str]:
    raw = str(getattr(container.env, "allowed_hosts", "") or "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def host_allowed(container, host_header: str) -> bool:
    """Does the Host header name this app — this machine, or a name the deployment gave it?

    DNS rebinding works by making a hostile domain resolve to 127.0.0.1 after the page has
    loaded: the browser then treats requests to this app as same-origin with the attacker.
    The one thing the attacker cannot change is the Host header the browser sends.
    """
    name = _hostname(host_header)
    if not name:
        return False
    if name in LOOPBACK or name.endswith(".localhost") or name.startswith("127."):
        return True
    for allowed in allowed_hosts(container):
        if allowed == "*" or name == allowed or (allowed.startswith(".") and name.endswith(allowed)):
            return True
    return False


def cross_site(request: Request) -> str:
    """Why this request looks like it was made by another site's page, or ''."""
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site in ("cross-site", "same-site"):
        return f"sent from another site ({site})"
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        try:
            netloc = urlparse(origin).netloc.lower()
        except ValueError:
            netloc = ""
        if origin == "null" or netloc != (request.headers.get("host") or "").lower():
            return f"sent from another origin ({origin[:80]})"
    return ""


def _prune(key: str) -> list[float]:
    cutoff = time.time() - ATTEMPT_WINDOW_S
    kept = [t for t in _ATTEMPTS.get(key, []) if t > cutoff]
    _ATTEMPTS[key] = kept
    return kept


def _same(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(
        hashlib.sha256((given or "").encode()).digest(), hashlib.sha256(expected.encode()).digest())


def _client_key(request: Request) -> str:
    forwarded = [h.strip() for h in ",".join(request.headers.getlist("x-forwarded-for")).split(",") if h.strip()]
    return (forwarded[-1] if forwarded else (request.client.host if request.client else "")) or "unknown"


def login(container, request: Request, password: str) -> dict:
    admin = (container.env.admin_password or "").strip()
    access = (container.env.access_password or "").strip()
    if not admin and not access:
        raise HTTPException(400, "No password is set — the app is open from this machine only.")
    key = _client_key(request)
    if len(_prune(key)) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    role = "admin" if _same(password, admin) else "user" if _same(password, access) else ""
    if not role:
        _ATTEMPTS.setdefault(key, []).append(time.time())
        raise HTTPException(401, "Wrong password.")
    _ATTEMPTS.pop(key, None)
    token = secrets.token_urlsafe(32)
    container.store.sessions()[_key(token)] = {"id": new_id("s"), "created_at": now(), "role": role,
                                         "expires_at": now() + container.env.session_ttl_s}
    container.store.touch()
    return {"token": token, "role": role, "expires_at": now() + container.env.session_ttl_s}


def _key(token: str) -> str:
    """Sessions are stored under a hash of their token: a copy of the store is not a login."""
    return "h:" + hashlib.sha256(token.encode()).hexdigest()


def logout(container, token: str) -> None:
    if container.store.sessions().pop(_key(token), None) is not None:
        container.store.touch()


def _session(container, token: str) -> dict | None:
    session = container.store.sessions().get(_key(token)) if token else None
    if session is None:
        return None
    if session.get("expires_at", 0) < now():
        container.store.sessions().pop(_key(token), None)
        container.store.touch()
        return None
    return session


def _valid_token(container, token: str) -> bool:
    return _session(container, token) is not None


def request_token(request: Request) -> str:
    """The session token a request carries: a bearer header, or the sign-in cookie.

    The cookie exists for what cannot carry a header — the event stream and download
    links. It is HttpOnly and SameSite=Strict, so no other site's page can use it.
    """
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.cookies.get(COOKIE) or "").strip()


def role_of(container, request: Request) -> str:
    """'admin', 'user' or '' for this request's session."""
    session = _session(container, request_token(request))
    if session is None:
        return ""
    return str(session.get("role") or "admin")   # sessions from before roles were admin


def check_app(container, request: Request) -> None:
    """Raise unless this request may use the app at all (chat, conversations, files)."""
    if is_local(request) or role_of(container, request):
        return
    if remote_mode(container) == "closed":
        raise HTTPException(
            403,
            "This agent answers only on the machine it runs on. To use it from other "
            "machines, set AGENT_ACCESS_PASSWORD (and AGENT_ADMIN_PASSWORD to administer it).")
    raise HTTPException(401, "Sign-in required.")


def check_admin(container, request: Request) -> None:
    """Raise unless this request may touch the admin area."""
    if mode(container) == "local-only":
        if is_local(request):
            return
        raise HTTPException(
            403,
            "Admin is restricted to the machine this runs on. To administer it from "
            "elsewhere, set AGENT_ADMIN_PASSWORD and restart.")
    if role_of(container, request) != "admin":
        raise HTTPException(401, "Admin sign-in required.")


def admin_state(container, request: Request) -> dict:
    """What the browser needs to decide whether to show the admin door — or a sign-in."""
    current = mode(container)
    local = is_local(request)
    role = role_of(container, request)
    signed_in = local or bool(role)
    authenticated = local if current == "local-only" else role == "admin"
    return {"mode": current, "authenticated": authenticated, "local": local,
            "signed_in": signed_in, "remote": remote_mode(container)}
