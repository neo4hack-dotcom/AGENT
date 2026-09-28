"""What the app keeps on disk, readable by the account that runs it — and by nobody else.

Conversations hold what the sources answered, the audit log what every tool did, `.env` the
passwords. Created with default permissions they are readable by the other people on the
machine: on Windows a folder made under `C:\\` grants every authenticated user access, and
on a shared server — Remote Desktop, Citrix — those are colleagues. So at every start the
app's own data folders and `.env` are narrowed to the current account, the system and the
administrators (Windows, by SID: group names are translated with the OS), or to the owner
alone (elsewhere).

Only what lives inside the app's folder is touched. A store placed elsewhere by the
deployment keeps the permissions the deployment gave it; Diagnostics says so.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
WINDOWS = sys.platform == "win32"
SYSTEM_SID, ADMINS_SID = "S-1-5-18", "S-1-5-32-544"

_REPORT: list[dict] = []


def _inside_app(path: Path) -> bool:
    """Strictly inside the app's folder — and not one of the folders the code itself lives in."""
    return APP_ROOT in path.parents and path not in (APP_ROOT / "backend", Path.cwd().resolve())


def _current_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    fields = [f.strip().strip('"') for f in (out.stdout or "").strip().split(",")]
    return fields[-1] if fields and fields[-1].startswith("S-1-") else ""


def _restrict_windows(path: Path, sid: str) -> str:
    inherit = ":(OI)(CI)F" if path.is_dir() else ":F"
    grants = [f"*{s}{inherit}" for s in (sid, SYSTEM_SID, ADMINS_SID)]
    out = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", *grants],
                         capture_output=True, text=True, timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "" if out.returncode == 0 else (out.stderr or out.stdout or "icacls failed").strip()[:200]


def _restrict_posix(path: Path) -> str:
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
        return ""
    except OSError as exc:
        return str(exc)


def private_paths(env) -> list[Path]:
    """The folders the app writes its data into, and `.env`."""
    folders = {Path(p).expanduser().resolve().parent
               for p in (env.db_path, env.memory_path, env.audit_path) if p}
    folders.add(Path(env.workspace_dir).expanduser().resolve())
    # The workspace usually sits inside the data folder, which already covers it.
    paths = sorted(f for f in folders if not any(other in f.parents for other in folders))
    dotenv = Path(".env").resolve()
    if dotenv.is_file():
        paths.append(dotenv)
    return paths


def restrict(env) -> list[dict]:
    """Narrow the data folders and `.env` to this account. Never raises: a failure is
    reported (Diagnostics), not allowed to stop the app from starting."""
    _REPORT.clear()
    sid = ""
    if WINDOWS:
        try:
            sid = _current_sid()
        except (OSError, subprocess.SubprocessError):
            sid = ""
    for path in private_paths(env):
        entry = {"path": str(path), "private": False, "detail": ""}
        if not _inside_app(path):
            entry["detail"] = "outside the app folder — permissions left as the deployment set them"
        elif WINDOWS and not sid:
            entry["detail"] = "could not read this account's SID (whoami)"
        else:
            try:
                error = _restrict_windows(path, sid) if WINDOWS else _restrict_posix(path)
            except (OSError, subprocess.SubprocessError) as exc:
                error = str(exc)
            entry["private"] = not error
            entry["detail"] = error or ("this account, SYSTEM and Administrators only" if WINDOWS
                                        else "owner only")
        _REPORT.append(entry)
    return list(_REPORT)


def report() -> list[dict]:
    """What the last `restrict` did, plus — outside Windows — what the modes say now."""
    if WINDOWS:
        return list(_REPORT)
    out = []
    for entry in _REPORT:
        try:
            mode = stat.S_IMODE(os.stat(entry["path"]).st_mode)
            out.append({**entry, "private": not (mode & 0o077)})
        except OSError:
            out.append(entry)
    return out


def shared_machine() -> str:
    """Why this looks like a machine several people use at once, or ""."""
    if not WINDOWS:
        return ""
    session = os.environ.get("SESSIONNAME", "")
    if session and session.lower() != "console":
        return f"a remote session ({session})"
    try:
        import platform
        edition = platform.win32_edition() or ""
    except (AttributeError, OSError):
        edition = ""
    return f"Windows {edition}" if "server" in edition.lower() else ""


def posture(env, settings, kernel_sandbox: bool) -> tuple[dict, list[str]]:
    """Every switch that bears on what the app can reach or run, and what to change."""
    loopback = env.host in ("127.0.0.1", "::1", "localhost")
    tls = bool(env.tls_cert.strip() and env.tls_key.strip())
    data = report()
    shared = shared_machine()
    state = {
        "airgapped": bool(env.airgapped),
        "kernel_sandbox": kernel_sandbox,
        "require_signin": bool(env.require_signin),
        "admin_password": bool(env.admin_password.strip()),
        "access_password": bool(env.access_password.strip()),
        "listens": "this machine" if loopback else env.host,
        "tls": tls,
        "python_tool": bool(settings.enable_python_tool),
        "python_tool_allowed": bool(env.enable_python_tool),
        "pandas": bool(env.enable_pandas),
        "custom_commands": bool(env.allow_custom_commands),
        "data_private": bool(data) and all(d["private"] for d in data),
        "data": data,
        "shared_machine": shared,
    }
    advice: list[str] = []
    if not env.airgapped:
        advice.append("AGENT_AIRGAPPED is off: models, MCP endpoints and packages may reach the internet.")
    if shared and not env.require_signin:
        advice.append(f"This is {shared}: everyone logged on to it counts as 'this machine', admin "
                      f"included. Set AGENT_REQUIRE_SIGNIN=true with AGENT_ADMIN_PASSWORD and "
                      f"AGENT_ACCESS_PASSWORD.")
    if not loopback and not tls:
        advice.append(f"The app listens on {env.host} over plain HTTP: passwords and answers cross the "
                      f"network unencrypted. Set AGENT_TLS_CERT and AGENT_TLS_KEY.")
    for label, value in (("AGENT_ADMIN_PASSWORD", env.admin_password), ("AGENT_ACCESS_PASSWORD", env.access_password)):
        if value.strip() and len(value.strip()) < 12:
            advice.append(f"{label} is shorter than 12 characters.")
    if settings.enable_python_tool and not kernel_sandbox:
        advice.append("run_python has no kernel sandbox on this OS: the code it runs is refused the "
                      "network in-process only, and can read what this account can. Where documents "
                      "from outside meet the agent, set AGENT_ENABLE_PYTHON_TOOL=false.")
    for entry in data:
        if not entry["private"]:
            advice.append(f"{entry['path']} is not narrowed to this account: {entry['detail']}.")
    return state, advice
