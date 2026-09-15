"""An append-only record of everything the agent did, chained so tampering shows.

Two reasons this exists, and the second is the one that made it worth writing.

The first is ordinary: when an agent does something surprising, the transcript shows what
it said and this shows what it *did* — which tool, with which arguments, under whose
authority, and whether the run had read untrusted content at the time.

The second is that the transcript is editable. It is a JSON file this process rewrites on
every message; anything that can write there can rewrite history, and an agent with file
access is something that can write there. So each entry carries the hash of the one before
it. That does not prevent tampering — nothing a process can write can prevent a process
from writing — but it makes it *visible*: change one line and every hash after it stops
matching, and `verify()` says where.

Deliberately not a database. One JSONL file, one line per action, readable with `tail`.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tip = self._last_hash()

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS
        tail = ""
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 8192))
                tail = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return GENESIS
        for line in reversed(tail.splitlines()):
            if line.strip():
                try:
                    return json.loads(line).get("hash", GENESIS)
                except json.JSONDecodeError:
                    continue
        return GENESIS

    @staticmethod
    def _digest(previous: str, body: str) -> str:
        return hashlib.sha256(f"{previous}\n{body}".encode()).hexdigest()

    def record(self, event: str, **fields) -> str:
        """Append one entry and return its hash.

        Never raises: an agent that cannot write its audit log still has work to do, and
        failing the run would make the log a single point of failure for the whole app.
        """
        entry = {"ts": round(time.time(), 3), "event": event, **fields}
        try:
            with self._lock:
                body = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
                entry["prev"] = self._tip
                entry["hash"] = self._digest(self._tip, body)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                self._tip = entry["hash"]
                return entry["hash"]
        except OSError:
            return ""

    def read(self, limit: int = 200, run_id: str = "") -> list[dict]:
        if not self.path.exists():
            return []
        entries: list[dict] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if run_id and entry.get("run_id") != run_id:
                    continue
                entries.append(entry)
        return entries[-limit:]

    def verify(self) -> dict:
        """Walk the chain. Reports the first entry whose hash does not follow from the one
        before it — which is where an edit, a truncation or a splice happened."""
        previous = GENESIS
        checked = 0
        if not self.path.exists():
            return {"ok": True, "entries": 0, "broken_at": None}
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    return {"ok": False, "entries": checked, "broken_at": number,
                            "reason": "line is not JSON"}
                body = json.dumps({k: v for k, v in entry.items() if k not in ("prev", "hash")},
                                  ensure_ascii=False, sort_keys=True, default=str)
                if entry.get("prev") != previous:
                    return {"ok": False, "entries": checked, "broken_at": number,
                            "reason": "does not follow the previous entry"}
                if entry.get("hash") != self._digest(previous, body):
                    return {"ok": False, "entries": checked, "broken_at": number,
                            "reason": "contents do not match the recorded hash"}
                previous = entry["hash"]
                checked += 1
        return {"ok": True, "entries": checked, "broken_at": None}
