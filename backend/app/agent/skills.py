"""Identity and skills: the two things this agent keeps that are not facts.

Memory answers *what is true* — "the shop database has fifteen orders". It does not answer
*how this gets done*, and the better self-hosted agent runtimes treat that as a separate
layer for a good reason: a procedure that worked is worth more than the facts it produced,
and it generalises to the next question of the same shape.

So there are two stores here, both deliberately small:

**Identity** — one document, first in the system prompt. Tone, standing preferences, the
things you would otherwise retype at the start of every conversation. It is not a prompt
the agent writes; it is yours, and nothing the agent reads can edit it.

**Skills** — procedures, not facts. "Load a CSV into the pandas server and describe it" is a
skill; "orders.csv has fifteen rows" is a memory. A skill is distilled only after the *same
shape of work* has succeeded three times, because a procedure written from one success is a
transcript, not a procedure — the third run is where what varies and what does not become
visible.

Both are retrieved the same way as memory, into the same prompt, and a learned skill is
fenced like anything else the agent wrote to itself.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time

from app.store import new_id

_TOKEN = re.compile(r"[\wÀ-ɏ]{2,}", re.UNICODE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    trigger     TEXT NOT NULL DEFAULT '',   -- when this applies, in the agent's words
    body        TEXT NOT NULL,              -- the procedure itself
    signature   TEXT NOT NULL DEFAULT '',   -- the tool sequence it was distilled from
    source      TEXT NOT NULL DEFAULT 'learned',  -- learned | user
    uses        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
    name, trigger, body, content='skills', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS skills_ai AFTER INSERT ON skills BEGIN
    INSERT INTO skills_fts(rowid, name, trigger, body)
    VALUES (new.rowid, new.name, new.trigger, new.body);
END;
CREATE TRIGGER IF NOT EXISTS skills_ad AFTER DELETE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, trigger, body)
    VALUES ('delete', old.rowid, old.name, old.trigger, old.body);
END;
CREATE TABLE IF NOT EXISTS traces (
    signature   TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 0,
    example     TEXT NOT NULL DEFAULT '',
    distilled   INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);
"""

DISTIL_SYSTEM = """You are writing a reusable procedure from three runs that did the same \
kind of work successfully.

Write what someone would need to do it again — not what happened. Strip the specifics: the \
particular file, the particular number, the particular question. Keep the order of \
operations, the tools involved, and anything that was non-obvious the first time.

Strict JSON only:
{"name": "<4-8 words, imperative>", "trigger": "<one sentence: when this applies>", \
"body": "<the procedure, 3-8 short numbered steps>"}

If the runs have nothing generalisable in common — they merely used the same tools for \
unrelated ends — return {"name": "", "trigger": "", "body": ""}. A procedure nobody can \
follow twice is worse than none, because it will be retrieved and believed."""


def trace_signature(tool_names: list[str]) -> str:
    """What *shape* of work a run was.

    The ordered sequence of distinct tools, hashed. Two runs that read a CSV then described
    it share a signature whatever the file; a run that also wrote a report does not, and
    should not — that is a different procedure.
    """
    ordered: list[str] = []
    for name in tool_names:
        if name not in ordered and name not in ("plan", "find_tools", "current_time"):
            ordered.append(name)
    if len(ordered) < 2:
        return ""
    return hashlib.sha1("→".join(ordered).encode()).hexdigest()[:16]


class Skills:
    """Skills and identity, sharing the memory database."""

    DISTIL_AFTER = 3

    def __init__(self, connection: sqlite3.Connection, store) -> None:
        self._db = connection
        self._db.executescript(SCHEMA)
        self._db.commit()
        self.store = store

    # ------------------------------------------------------------------- identity
    def soul(self) -> str:
        return str(self.store.prefs().get("soul") or "").strip()

    def set_soul(self, text: str) -> str:
        self.store.set_prefs({"soul": (text or "").strip()[:4000]})
        return self.soul()

    # --------------------------------------------------------------------- skills
    def add(self, name: str, trigger: str, body: str, *, source: str = "user",
            signature: str = "") -> dict:
        row = {"id": new_id("s"), "name": name.strip()[:120], "trigger": trigger.strip()[:400],
               "body": body.strip()[:4000], "signature": signature, "source": source,
               "uses": 0, "created_at": time.time()}
        self._db.execute(
            "INSERT INTO skills (id, name, trigger, body, signature, source, uses, created_at) "
            "VALUES (:id, :name, :trigger, :body, :signature, :source, :uses, :created_at)", row)
        self._db.commit()
        return row

    def forget(self, skill_id: str) -> bool:
        cursor = self._db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def all(self) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT * FROM skills ORDER BY uses DESC, created_at DESC")]

    def recall(self, query: str, limit: int = 3) -> list[dict]:
        terms = [t.lower() for t in _TOKEN.findall(query or "")]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms[:24])
        try:
            rows = self._db.execute(
                "SELECT s.* FROM skills_fts f JOIN skills s ON s.rowid = f.rowid "
                "WHERE skills_fts MATCH ? ORDER BY bm25(skills_fts) LIMIT ?",
                (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        found = [dict(r) for r in rows]
        if found:
            self._db.executemany("UPDATE skills SET uses = uses + 1 WHERE id = ?",
                                 [(r["id"],) for r in found])
            self._db.commit()
        return found

    # ------------------------------------------------------- learning from success
    def note_success(self, tool_names: list[str], question: str) -> str:
        """Record that this shape of work succeeded. Returns a signature ready to distil.

        Nothing is distilled from one run: a procedure written from a single success is a
        transcript with the specifics filed off, and it will be retrieved for work it does
        not actually fit.
        """
        signature = trace_signature(tool_names)
        if not signature:
            return ""
        row = self._db.execute("SELECT * FROM traces WHERE signature = ?",
                               (signature,)).fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO traces (signature, count, example, distilled, updated_at) "
                "VALUES (?, 1, ?, 0, ?)", (signature, question[:500], time.time()))
            self._db.commit()
            return ""
        if row["distilled"]:
            return ""
        count = row["count"] + 1
        self._db.execute("UPDATE traces SET count = ?, updated_at = ? WHERE signature = ?",
                         (count, time.time(), signature))
        self._db.commit()
        return signature if count >= self.DISTIL_AFTER else ""

    def mark_distilled(self, signature: str) -> None:
        self._db.execute("UPDATE traces SET distilled = 1 WHERE signature = ?", (signature,))
        self._db.commit()

    def trace_example(self, signature: str) -> str:
        row = self._db.execute("SELECT example FROM traces WHERE signature = ?",
                               (signature,)).fetchone()
        return row["example"] if row else ""

    # --------------------------------------------------------------------- prompt
    def prompt_block(self, question: str, nonce: str = "") -> str:
        """Identity first, then whichever procedures match — the order matters.

        Identity is yours and goes in unfenced. Skills the agent wrote for itself are
        fenced, for the same reason memories are: by the time one is retrieved, nothing
        distinguishes a procedure it worked out from one it was talked into writing.
        """
        parts: list[str] = []
        soul = self.soul()
        if soul:
            parts.append(f"## Who you are here\n\n{soul}")
        matched = self.recall(question)
        if matched:
            body = "\n\n".join(
                f"**{s['name']}** — {s['trigger']}\n{s['body']}" for s in matched)
            if nonce:
                from app.agent import trust
                body = trust.fence(nonce, "your own skills", body)
            parts.append(
                f"## Procedures you worked out earlier\n\n{body}\n\n"
                f"Follow one only if it genuinely fits what was asked. A procedure that "
                f"nearly fits is how the wrong thing gets done efficiently.")
        return ("\n\n" + "\n\n".join(parts)) if parts else ""

    def stats(self) -> dict:
        row = self._db.execute(
            "SELECT COUNT(*) total, SUM(source = 'learned') learned FROM skills").fetchone()
        pending = self._db.execute(
            "SELECT COUNT(*) n FROM traces WHERE distilled = 0 AND count >= ?",
            (self.DISTIL_AFTER,)).fetchone()["n"]
        return {"total": row["total"] or 0, "learned": row["learned"] or 0,
                "ready": pending or 0}
