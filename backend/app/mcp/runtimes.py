"""Launchers a *custom* server may need, and whether this machine has them.

Nothing the app ships needs one: every catalog server runs on the app's own Python. A
custom stdio server started through `npx` or `uvx` does — and in a private network those
launchers can only start packages already provisioned from an internal mirror or cache.
Only launchers some configured server actually uses are reported.
"""

from __future__ import annotations

import os
import shutil
import sys

RUNTIMES: dict[str, dict[str, str]] = {
    "npx": {
        "label": "Node.js",
        "why": "starts npm-packaged MCP servers",
        "install_macos": "install Node.js from your internal software catalogue",
        "install_other": "install Node.js from your internal software catalogue",
        "url": "",
    },
    "uvx": {
        "label": "uv",
        "why": "starts Python-packaged MCP servers (uvx ships with uv)",
        "install_macos": "install uv from your internal software catalogue",
        "install_other": "install uv from your internal software catalogue",
        "url": "",
    },
}


def install_hint(command: str) -> str:
    """The one command that would make ``command`` exist on this machine."""
    runtime = RUNTIMES.get(command)
    if runtime is None:
        return ""
    return runtime["install_macos"] if sys.platform == "darwin" else runtime["install_other"]


def probe(used: set[str] | None = None) -> dict[str, dict]:
    """Resolve each launcher against the PATH a spawned server will actually inherit.

    Looking it up any other way would report on the wrong environment: a launcher present
    in your login shell but missing from the one that started the API is exactly the case
    this needs to catch.
    """
    path = os.environ.get("PATH", "")
    out: dict[str, dict] = {}
    for name, meta in RUNTIMES.items():
        if used is not None and name not in used:
            continue
        resolved = shutil.which(name, path=path)
        out[name] = {
            "name": name,
            "label": meta["label"],
            "why": meta["why"],
            "available": resolved is not None,
            "path": resolved or "",
            "install": install_hint(name),
            "url": meta["url"],
        }
    return out


def required_by(entry: dict) -> str:
    """The launcher a catalog entry needs, or "" for a remote server that needs none."""
    command = entry.get("command") or ""
    return command if command in RUNTIMES else ""
