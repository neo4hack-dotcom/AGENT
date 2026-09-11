"""The loop guard: what keeps an autonomous loop bounded.

Two distinct failure modes, two mechanisms:

* **blind repetition** — the model retries the same call, or "tries something different"
  that is the same call reworded. Caught by a per-signature failure streak, where the
  signature is the step's *structural* target (tool + arguments), never its prose.
  Textual novelty is not strategic novelty, and a signature that included the wording
  would never fire on the case this exists to catch.
* **unbounded wandering** — every call looks new, the loop simply never ends. Caught by
  hard ceilings on attempts and wall clock, which hold regardless of repetition.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

# Arguments that carry a free-text intent rather than an exact address. Two searches whose
# words are the same in a different order are the same search, and a loop that cannot see
# that will happily re-run it until its budget is gone.
QUERY_KEYS = {"query", "q", "search", "search_query", "question", "term", "keywords", "prompt"}
# What a call is *about*, as opposed to how much of it to return. Nine reads of one file
# under nine combinations of head/tail/offset are nine calls with nine signatures and one
# subject — and the loop spends its budget re-reading what it already has.
SUBJECT_KEYS = ("path", "file", "filename", "file_path", "url", "uri", "name", "table_name")


def _canonical_query(text: str) -> str:
    words = sorted(set(re.findall(r"[a-z0-9à-ÿ]+", text.lower())))
    return " ".join(words)


def signature(name: str, arguments: dict | None) -> str:
    """Canonical fingerprint of "the same idea": the tool plus its arguments.

    Exact for everything addressable (a URL, a path, a snippet of code), word-set based
    for free-text queries — where "latest stable Python release date" and "Python release
    date latest stable" are one idea wearing two hats.
    """
    args = {}
    for key, value in (arguments or {}).items():
        if key in QUERY_KEYS and isinstance(value, str):
            args[key] = _canonical_query(value)
        else:
            args[key] = value
    raw = json.dumps({"tool": name, "args": args}, sort_keys=True,
                     ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class LoopGuard:
    def __init__(self, max_total_steps: int, timeout_s: int, stagnation_limit: int = 2) -> None:
        self.max_total_steps = max_total_steps
        self.timeout_s = timeout_s
        self.stagnation_limit = max(1, stagnation_limit)
        self.started_at = time.time()
        self._paused_at: float | None = None
        self.total_attempts = 0
        self._fail_streak: dict[str, int] = {}
        self._seen: dict[str, int] = {}
        # Per *tool*, not per signature. A rate-limited search fails under eight different
        # queries with eight different signatures, so the structural streak never fires —
        # and the loop happily spends its whole budget rephrasing a question to a service
        # that is not answering anyone. What is broken there is the tool, not the idea.
        self._tool_fail_streak: dict[str, int] = {}
        self._subjects: dict[str, int] = {}

    def record(self, name: str, arguments: dict | None, ok: bool) -> None:
        self.total_attempts += 1
        sig = signature(name, arguments)
        self._seen[sig] = self._seen.get(sig, 0) + 1
        self._fail_streak[sig] = 0 if ok else self._fail_streak.get(sig, 0) + 1
        self._tool_fail_streak[name] = 0 if ok else self._tool_fail_streak.get(name, 0) + 1
        subject = self._subject(name, arguments)
        if subject:
            self._subjects[subject] = self._subjects.get(subject, 0) + 1

    def is_stagnant(self, name: str, arguments: dict | None) -> bool:
        return self._fail_streak.get(signature(name, arguments), 0) >= self.stagnation_limit

    def subject_repeats(self, name: str, arguments: dict | None) -> int:
        """How many times this tool was already pointed at this same subject."""
        subject = self._subject(name, arguments)
        return self._subjects.get(subject, 0) if subject else 0

    @staticmethod
    def _subject(name: str, arguments: dict | None) -> str:
        for key in SUBJECT_KEYS:
            value = (arguments or {}).get(key)
            if isinstance(value, str) and value.strip():
                return f"{name}:{key}={value.strip()}"
        return ""

    def tool_is_down(self, name: str, limit: int = 3) -> bool:
        """True once a tool has failed `limit` times in a row on any arguments."""
        return self._tool_fail_streak.get(name, 0) >= limit

    def tool_failures(self, name: str) -> int:
        return self._tool_fail_streak.get(name, 0)

    def repeat_count(self, name: str, arguments: dict | None) -> int:
        """How many times this exact call already ran — including the ones that worked.

        A *successful* call repeated verbatim is its own kind of stall: the model has
        stopped making progress and is re-reading what it already has.
        """
        return self._seen.get(signature(name, arguments), 0)

    def pause(self) -> None:
        """Stop the clock while a human decides.

        The wall-clock budget exists to bound the *agent*, not the person approving a
        step. Without this, a run that waits three minutes for a yes has three minutes
        less to do the work it was approved for — and can time out having done nothing
        but wait.
        """
        if self._paused_at is None:
            self._paused_at = time.time()

    def resume(self) -> None:
        if self._paused_at is not None:
            self.started_at += time.time() - self._paused_at
            self._paused_at = None

    def elapsed_s(self) -> float:
        reference = self._paused_at if self._paused_at is not None else time.time()
        return reference - self.started_at

    def over_step_budget(self) -> bool:
        return self.max_total_steps > 0 and self.total_attempts >= self.max_total_steps

    def over_time_budget(self) -> bool:
        return self.timeout_s > 0 and self.elapsed_s() >= self.timeout_s

    def remaining_s(self) -> float:
        return max(0.0, self.timeout_s - self.elapsed_s())

    def snapshot(self) -> dict:
        return {"steps_used": self.total_attempts, "max_steps": self.max_total_steps,
                "elapsed_s": round(self.elapsed_s(), 1), "timeout_s": self.timeout_s}
