"""Flat-JSON persistence.

One process, one file, no migrations — genuinely enough for a single-user local agent.
Everything goes through this object, so swapping in SQLite later touches one file.

Two details that matter in an app that streams: writes are serialised behind a lock, and
they are *debounced* — a run emits hundreds of deltas per second and none of them are
worth an fsync. The transcript is flushed when a message completes, not per token.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

EMPTY: dict[str, Any] = {
    "version": 1,
    "conversations": {},
    "mcp_servers": {},
    "prefs": {},
    "memory": [],
    "sessions": {},
}


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


def now() -> float:
    return time.time()


def _replace(source: Path, target: Path) -> None:
    """os.replace, retried: on Windows an antivirus or an indexer holding the target for a
    moment makes the swap fail with PermissionError, and a lost save is worse than a wait."""
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


class JsonStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = dict(EMPTY)
        if self.path.exists():
            try:
                # UTF-8 explicitly: Windows would otherwise read it as cp1252 and every accent
                # in the store would fail or turn to mojibake. -sig tolerates a BOM from an
                # editor that added one.
                loaded = json.loads(self.path.read_text(encoding="utf-8-sig") or "{}")
                self.data = {**EMPTY, **loaded}
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                # A corrupted store must not stop the app from starting. Keep the broken
                # file next to the fresh one so nothing is silently destroyed.
                backup = self.path.with_suffix(f".corrupt-{int(now())}.json")
                try:
                    self.path.rename(backup)
                except OSError:
                    pass
        self._lock = asyncio.Lock()
        self._dirty = False
        self._flusher: asyncio.Task | None = None

    async def save(self) -> None:
        """Write now, atomically."""
        async with self._lock:
            self._dirty = False
            payload = json.dumps(self.data, ensure_ascii=False, indent=2, default=str)
            tmp = self.path.with_suffix(".tmp")
            await asyncio.to_thread(tmp.write_text, payload, encoding="utf-8")
            await asyncio.to_thread(_replace, tmp, self.path)

    def touch(self) -> None:
        """Mark dirty; the background flusher persists within a second."""
        self._dirty = True

    async def start_flusher(self, interval_s: float = 1.0) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                if self._dirty:
                    try:
                        await self.save()
                    except OSError:
                        pass

        self._flusher = asyncio.create_task(loop())

    async def stop_flusher(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
            self._flusher = None
        if self._dirty:
            await self.save()

    # --- Conversations -------------------------------------------------------
    def conversations(self) -> dict[str, Any]:
        return self.data.setdefault("conversations", {})

    def conversation(self, conv_id: str) -> dict | None:
        return self.conversations().get(conv_id)

    def create_conversation(self, title: str = "") -> dict:
        conv = {
            "id": new_id("c"),
            "title": title,
            "created_at": now(),
            "updated_at": now(),
            "messages": [],
        }
        self.conversations()[conv["id"]] = conv
        self.touch()
        return conv

    def append_message(self, conv_id: str, message: dict) -> dict:
        conv = self.conversations().get(conv_id)
        if conv is None:
            raise KeyError(conv_id)
        conv["messages"].append(message)
        conv["updated_at"] = now()
        self.touch()
        return message

    def conversation_list(self) -> list[dict]:
        out = []
        for conv in self.conversations().values():
            messages = conv.get("messages") or []
            out.append({
                "id": conv["id"],
                "title": conv.get("title") or "",
                "created_at": conv.get("created_at"),
                "updated_at": conv.get("updated_at"),
                "message_count": len(messages),
                "preview": next((m.get("content", "")[:120] for m in messages if m.get("role") == "user"), ""),
            })
        return sorted(out, key=lambda c: c.get("updated_at") or 0, reverse=True)

    # --- Preferences (UI-set values that override the env) -------------------
    def prefs(self) -> dict[str, Any]:
        return self.data.setdefault("prefs", {})

    def set_prefs(self, values: dict[str, Any]) -> dict[str, Any]:
        prefs = self.prefs()
        for key, value in values.items():
            if value is None:
                prefs.pop(key, None)
            else:
                prefs[key] = value
        self.touch()
        return prefs

    # --- MCP servers ---------------------------------------------------------
    def mcp_servers(self) -> dict[str, Any]:
        return self.data.setdefault("mcp_servers", {})

    # --- Long-term memory ----------------------------------------------------
    def memory(self) -> list[dict]:
        return self.data.setdefault("memory", [])

    # --- Admin sessions ------------------------------------------------------
    def sessions(self) -> dict[str, Any]:
        return self.data.setdefault("sessions", {})
