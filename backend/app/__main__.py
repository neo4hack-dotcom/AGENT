"""`python -m app` — the API, and the built interface with it, as the deployment configured it.

Host, port and TLS come from the same settings as everything else (backend/.env), so a
deployment is described in one file rather than split between it and a launch command.
Never with --reload: on Windows the reloader switches asyncio to the selector loop, which
cannot start the processes run_python and every local MCP server are.
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import settings

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def main() -> None:
    cert, key = settings.tls_cert.strip(), settings.tls_key.strip()
    if bool(cert) != bool(key):
        sys.exit("[AGENT] AGENT_TLS_CERT and AGENT_TLS_KEY go together: set both, or neither.")
    options: dict = {
        "host": settings.host, "port": settings.port,
        # The forwarded-for header is believed from the local dev proxy only, whatever
        # FORWARDED_ALLOW_IPS says: from anyone else it would let a client pick its address.
        "proxy_headers": True, "forwarded_allow_ips": "127.0.0.1",
        "server_header": False,
    }
    if cert:
        options.update(ssl_certfile=cert, ssl_keyfile=key)
    scheme = "https" if cert else "http"
    shown = "localhost" if settings.host in _LOOPBACK else settings.host
    print(f"[AGENT] {scheme}://{shown}:{settings.port}", flush=True)
    if settings.host not in _LOOPBACK and not cert:
        print("[AGENT] Listening beyond this machine without TLS: passwords and answers cross the "
              "network unencrypted. Set AGENT_TLS_CERT and AGENT_TLS_KEY.", flush=True)
    uvicorn.run("app.main:app", **options)


if __name__ == "__main__":
    main()
