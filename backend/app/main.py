"""The FastAPI app: routers first, then the built SPA behind them.

In development the frontend runs on its own port and proxies /api here. In production
there is no second process — the same app serves the built SPA from the same origin. In
both cases every request the browser makes is same-origin, so there is no CORS at all:
a CORS policy could only ever widen who may read this API, never narrow it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import security
from app.api.admin import guarded as admin_guarded, router as admin_router
from app.api.routes import router as api_router
from app.deps import container as c


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await c.store.start_flusher()
    c.workspace()
    if c.settings.mcp_autoconnect:
        # In the background: one slow server must not delay the first page load.
        asyncio.create_task(c.mcp.connect_enabled())
    else:
        c.mcp._ready.set()
    # The chart renderer starts a JavaScript engine on first use (~0.5s). Pay that at
    # boot, in the background, rather than in the middle of someone's first answer.
    from app.data.charts import warm_up
    asyncio.create_task(asyncio.to_thread(warm_up))
    try:
        yield
    finally:
        await c.mcp.shutdown()
        await c.store.stop_flusher()


app = FastAPI(title=c.env.app_name, version="1.0.0", lifespan=lifespan)

# Reachable without a session even from elsewhere: what the sign-in screen itself needs.
_OPEN = {"/api/health", "/api/admin/state", "/api/admin/login", "/api/admin/logout"}

# Headers every response carries. The page may load and connect to this origin only: an
# answer that renders a link or an image cannot make the browser send data anywhere else,
# which is the path out an injected instruction would try first.
_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}


def _csp(script_hashes: list[str]) -> str:
    # 'unsafe-eval' is Vega's expression compiler; there is no inline script without a hash.
    scripts = " ".join(["'self'", "'unsafe-eval'", *(f"'sha256-{h}'" for h in script_hashes)])
    return ("default-src 'self'; "
            f"script-src {scripts}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            "connect-src 'self'; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _refuse(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Three checks before any route runs, cheapest first.

    The Host must name this app (DNS rebinding), the request must not come from another
    site's page (a browser on this machine is otherwise a proxy for every tab it has open),
    and someone on another machine must have signed in. See app/security.py.
    """
    path = request.url.path
    if not security.host_allowed(c, request.headers.get("host") or ""):
        return _refuse(421, "This host name is not one this app answers to. If it is yours, "
                            "add it to AGENT_ALLOWED_HOSTS.")
    if path.startswith("/api"):
        reason = security.cross_site(request)
        if reason:
            c.audit.record("request.cross_site", path=path, reason=reason)
            return _refuse(403, f"Refused: this request was {reason}.")
        if path not in _OPEN:
            try:
                security.check_app(c, request)
            except HTTPException as exc:
                return _refuse(exc.status_code, str(exc.detail))
    response = await call_next(request)
    for name, value in _HEADERS.items():
        response.headers.setdefault(name, value)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", _csp(INLINE_SCRIPTS))
    return response


app.include_router(api_router)
app.include_router(admin_router)
app.include_router(admin_guarded)


def _static_dir() -> Path:
    if c.env.static_dir:
        return Path(c.env.static_dir).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


STATIC_DIR = _static_dir()


def _inline_script_hashes(index: Path) -> list[str]:
    """CSP hashes of the inline scripts in the built page (the theme set before first paint)."""
    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        return []
    return [base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)]


INLINE_SCRIPTS = _inline_script_hashes(STATIC_DIR / "index.html")

if (STATIC_DIR / "index.html").is_file():
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def spa_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        # A mistyped API path is a real 404, never swallowed into the SPA — otherwise it
        # returns HTML that fails to parse as JSON three layers up.
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = (STATIC_DIR / path).resolve()
        if candidate.is_file() and STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
