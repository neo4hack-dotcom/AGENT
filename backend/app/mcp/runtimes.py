"""Which launchers the catalog needs, and whether this machine actually has them.

Most stdio MCP servers are not installed at all: they are fetched on demand by a
launcher — `npx` for the npm ones, `uvx` for the Python ones. If the launcher is missing,
every server that depends on it fails at connect time with a message about a command
nobody deliberately installed. Detecting it up front turns that into one sentence on the
card, before the click.
"""

from __future__ import annotations

import os
import shutil
import sys

RUNTIMES: dict[str, dict[str, str]] = {
    "npx": {
        "label": "Node.js",
        "why": "runs the npm-published MCP servers",
        # Homebrew is the path of least resistance on macOS; the site covers everyone else.
        "install_macos": "brew install node",
        "install_other": "Install Node.js 18 or newer",
        "url": "https://nodejs.org",
    },
    "uvx": {
        "label": "uv",
        # The trap worth naming explicitly: the missing command is uvx, the thing you
        # install is uv. Telling someone to "install uvx" sends them nowhere.
        "why": "runs the Python-published MCP servers (uvx ships with uv)",
        "install_macos": "brew install uv",
        "install_other": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "url": "https://docs.astral.sh/uv/",
    },
}


def install_hint(command: str) -> str:
    """The one command that would make ``command`` exist on this machine."""
    runtime = RUNTIMES.get(command)
    if runtime is None:
        return ""
    return runtime["install_macos"] if sys.platform == "darwin" else runtime["install_other"]


def probe() -> dict[str, dict]:
    """Resolve each launcher against the PATH a spawned server will actually inherit.

    Looking it up any other way would report on the wrong environment: a launcher present
    in your login shell but missing from the one that started the API is exactly the case
    this needs to catch.
    """
    path = os.environ.get("PATH", "")
    out: dict[str, dict] = {}
    for name, meta in RUNTIMES.items():
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
