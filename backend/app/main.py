"""The FastAPI app: routers first, then the built SPA behind them.

In development the frontend runs on its own port and proxies /api here. In production
there is no second process — the same app serves the built SPA from the same origin, so
there is no CORS to configure and one thing to run.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    try:
        yield
    finally:
        await c.mcp.shutdown()
        await c.store.stop_flusher()


app = FastAPI(title=c.env.app_name, version="1.0.0", lifespan=lifespan)

# Exercised only in development, where Vite serves the UI from another port. Harmless in
# production, which never crosses origins.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["*"])

app.include_router(api_router)
app.include_router(admin_router)
app.include_router(admin_guarded)


def _static_dir() -> Path:
    if c.env.static_dir:
        return Path(c.env.static_dir).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


STATIC_DIR = _static_dir()

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
