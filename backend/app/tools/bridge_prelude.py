"""The child side of the tool bridge — prepended to code the agent writes.

This module is never imported by the app. Its *text* is read and pushed into the sandboxed
interpreter, where it defines `call_tool` and one named wrapper per available tool. Keeping
it a real file rather than a string literal means it is syntax-checked like everything else,
and readable by anyone wondering what the agent's code can actually reach.

The protocol is one JSON object per line over two inherited pipes. Pipes, specifically:
they survive the Seatbelt profile that denies the network, because a pipe is neither a
socket nor a file path.
"""

import json as _json
import os as _os
import sys as _sys

if _sys.platform == "win32":
    # On Windows the parent passes the pipes' OS handles, inherited explicitly; they become
    # file descriptors here. Elsewhere the numbers are inherited descriptors already.
    import msvcrt as _msvcrt
    _W = _msvcrt.open_osfhandle(int(_sys.argv[1]), _os.O_WRONLY)
    _R = _msvcrt.open_osfhandle(int(_sys.argv[2]), _os.O_RDONLY)
else:
    _W, _R = int(_sys.argv[1]), int(_sys.argv[2])
_buf = b""


class ToolError(RuntimeError):
    """A tool refused or failed. The message is what the tool itself said."""


def call_tool(_name, **kwargs):
    """Call one of the agent's tools and get its result as text.

    Raises ToolError on failure, so ordinary try/except works and a failed step cannot
    quietly become an empty string three lines further down.
    """
    global _buf
    _os.write(_W, (_json.dumps({"tool": _name, "args": kwargs}) + "\n").encode())
    while b"\n" not in _buf:
        chunk = _os.read(_R, 65536)
        if not chunk:
            raise ToolError("the agent closed the tool bridge")
        _buf += chunk
    line, _, _buf = _buf.partition(b"\n")
    reply = _json.loads(line)
    if not reply.get("ok"):
        raise ToolError(reply.get("error") or "the tool failed without a message")
    return reply.get("text") or ""
