"""The air gap, enforced rather than merely respected.

Offering nothing external is not the same as preventing it. An agent in a bank's private
network can still be pointed at a cloud model, handed an MCP server whose package fetches
URLs, or configured with an HTTP endpoint on the internet; and `npx`/`uvx` quietly
download packages from public registries the first time a server starts. So in air-gapped
mode — the default, and a property of the deployment rather than a switch in the UI —
every path out is checked where it opens:

- the model: no Ollama cloud models, no Ollama host outside the private network;
- MCP over HTTP: the endpoint must be internal;
- MCP over stdio: package managers run offline, packages whose job is the internet are
  refused, and on macOS the process is started inside a kernel sandbox that allows the
  loopback interface and nothing else — unless the server needs an internal host (a
  database client), which is then the only kind left with the network.

What cannot be enforced from here — a database client talking to an internal host — is
left to the enterprise firewall, and this module says so rather than implying otherwise.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Packages whose purpose is to reach the internet. Refused by name in air-gapped mode:
# a local sandbox would stop them, but a clear refusal beats a server that starts and
# fails on every call.
NETWORK_PACKAGES = (
    "mcp-server-fetch", "@modelcontextprotocol/server-fetch", "@modelcontextprotocol/server-puppeteer",
    "@playwright/mcp", "@modelcontextprotocol/server-brave-search", "@modelcontextprotocol/server-github",
    "@modelcontextprotocol/server-gitlab", "@modelcontextprotocol/server-slack", "mcp-server-git",
    "@modelcontextprotocol/server-google-maps", "@modelcontextprotocol/server-everything",
    "exa-mcp-server", "tavily-mcp", "firecrawl-mcp",
)

# Everything a package manager or library might otherwise do on first run.
OFFLINE_ENV = {
    "UV_OFFLINE": "1", "npm_config_offline": "true", "NPM_CONFIG_OFFLINE": "true",
    "PIP_NO_INDEX": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    "NO_UPDATE_NOTIFIER": "1", "npm_config_update_notifier": "false",
    "DO_NOT_TRACK": "1", "NEXT_TELEMETRY_DISABLED": "1",
}

SANDBOX = "/usr/bin/sandbox-exec"
_LOCAL_ONLY_PROFILE = """(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote unix-socket))
(allow network-outbound (remote ip "localhost:*"))"""

_URL_HOST = re.compile(r"(?:[a-z][a-z0-9+.-]*://)(?:[^@/\s]*@)?([^:/?#\s]+)", re.IGNORECASE)
_KV_HOST = re.compile(r"\b(?:host|hostname|server|dsn)\s*=\s*([A-Za-z0-9_.-]+)", re.IGNORECASE)
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


def _settings():
    from app.deps import container
    return container.settings


def airgapped() -> bool:
    return bool(_settings().airgapped)


def internal_suffixes() -> list[str]:
    raw = str(_settings().internal_domains or "")
    return [s.strip().lower().lstrip("*") for s in raw.split(",") if s.strip()]


def is_internal_host(host: str) -> bool:
    """Loopback, a private address, or a name the deployment declared internal.

    A name is resolved and every address it resolves to must be private: a name that
    resolves to the public internet is not internal because it is spelled like it is.
    """
    host = (host or "").strip().strip("[]").lower()
    if not host:
        return False
    if host in _LOCAL_NAMES or host.endswith(".localhost"):
        return True
    if any(host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s)
           for s in internal_suffixes()):
        return True
    return _resolves_inside(host)


_RESOLVED: dict[str, tuple[float, bool]] = {}


def _resolves_inside(host: str) -> bool:
    cached = _RESOLVED.get(host)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
        inside = bool(addresses) and all(
            ipaddress.ip_address(a.split("%")[0]).is_private or ipaddress.ip_address(a.split("%")[0]).is_loopback
            for a in addresses)
    except (socket.gaierror, UnicodeError, OSError, ValueError):
        inside = False
    _RESOLVED[host] = (time.monotonic(), inside)
    return inside


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


# ------------------------------------------------------------------- the model

def is_cloud_model(model: str) -> bool:
    name = (model or "").lower()
    return name.endswith(":cloud") or name.endswith("-cloud") or ":cloud-" in name


def check_model(model: str, base_url: str) -> str | None:
    """Why this model may not be used here, or None."""
    if not airgapped() or bool(_settings().allow_cloud_model):
        return None
    if is_cloud_model(model):
        return (f"{model} is an Ollama cloud model: prompts and data would leave the private "
                f"network for ollama.com. This deployment is air-gapped — pick a model that "
                f"runs on your own Ollama server.")
    host = _host_of(base_url)
    if host and not is_internal_host(host):
        return (f"The Ollama server {host} is outside the private network. This deployment is "
                f"air-gapped — point AGENT at an Ollama server on this machine or your "
                f"internal network.")
    return None


# ----------------------------------------------------------------- MCP servers

def network_policy(server: dict) -> str:
    """'local' (loopback only) or 'internal' (may reach internal hosts).

    Explicit configuration wins. Otherwise a server whose arguments or environment name a
    host other than this machine — a database URL, a host= setting — needs the network
    to reach it; everything else, which is nearly every stdio server, needs none.
    """
    configured = str(server.get("network") or "").lower()
    if configured in ("local", "internal"):
        return configured
    if server.get("transport") == "http":
        return "internal"
    blob = " ".join([*(str(a) for a in server.get("args") or []),
                     *(str(v) for v in (server.get("env") or {}).values())])
    hosts = [m.group(1) for m in _URL_HOST.finditer(blob)] + [m.group(1) for m in _KV_HOST.finditer(blob)]
    remote = [h for h in hosts if h.lower() not in _LOCAL_NAMES and not h.lower().endswith(".localhost")]
    return "internal" if remote else "local"


def check_mcp(server: dict) -> str | None:
    """Why this server may not be started here, or None."""
    if not airgapped():
        return None
    if server.get("transport") == "http":
        host = _host_of(server.get("url") or "")
        if not is_internal_host(host):
            return (f"{server.get('name')}: {host or 'this URL'} is outside the private network. "
                    f"This deployment is air-gapped — only internal MCP endpoints can be used. "
                    f"If it is internal, add its domain to AGENT_INTERNAL_DOMAINS.")
        return None
    launch = " ".join([str(server.get("command") or ""), *(str(a) for a in server.get("args") or [])])
    for package in NETWORK_PACKAGES:
        if re.search(rf"(^|[\s/@]){re.escape(package)}(@[\w.^~-]+)?(\s|$)", launch):
            return (f"{server.get('name')}: {package} exists to reach the internet, which this "
                    f"air-gapped deployment does not allow.")
    for value in [*(server.get("args") or []), *((server.get("env") or {}).values())]:
        for match in _URL_HOST.finditer(str(value)):
            host = match.group(1)
            if host.lower() not in _LOCAL_NAMES and not is_internal_host(host):
                return (f"{server.get('name')}: its configuration points at {host}, outside the "
                        f"private network. This deployment is air-gapped.")
    return None


def stdio_launch(command: str, args: list[str], env: dict[str, str],
                 server: dict) -> tuple[str, list[str], dict[str, str], str]:
    """The command actually run for a stdio server, and a one-line account of the fence.

    Offline package managers always; the loopback-only kernel sandbox on macOS for every
    server that does not need an internal host.
    """
    if not airgapped():
        return command, args, env, "network open (not air-gapped)"
    env = {**OFFLINE_ENV, **env}
    policy = network_policy(server)
    if policy == "local" and sys.platform == "darwin" and Path(SANDBOX).exists():
        return SANDBOX, ["-p", _LOCAL_ONLY_PROFILE, command, *args], env, \
            "loopback only, enforced by the kernel"
    if policy == "local":
        return command, args, env, "loopback only by configuration (no kernel sandbox on this OS)"
    return command, args, env, "internal network (enforced by the enterprise firewall)"


def summary() -> dict:
    """What Diagnostics shows about the air gap."""
    return {"airgapped": airgapped(), "internal_domains": internal_suffixes(),
            "kernel_sandbox": sys.platform == "darwin" and Path(SANDBOX).exists(),
            "cloud_model_allowed": bool(_settings().allow_cloud_model)}
