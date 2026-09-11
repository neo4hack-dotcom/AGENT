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
        self.total_attempts = 0
        self._fail_streak: dict[str, int] = {}
        self._seen: dict[str, int] = {}

    def record(self, name: str, arguments: dict | None, ok: bool) -> None:
        self.total_attempts += 1
        sig = signature(name, arguments)
        self._seen[sig] = self._seen.get(sig, 0) + 1
        self._fail_streak[sig] = 0 if ok else self._fail_streak.get(sig, 0) + 1

    def is_stagnant(self, name: str, arguments: dict | None) -> bool:
        return self._fail_streak.get(signature(name, arguments), 0) >= self.stagnation_limit

    def repeat_count(self, name: str, arguments: dict | None) -> int:
        """How many times this exact call already ran — including the ones that worked.

        A *successful* call repeated verbatim is its own kind of stall: the model has
        stopped making progress and is re-reading what it already has.
        """
        return self._seen.get(signature(name, arguments), 0)

    def over_step_budget(self) -> bool:
        return self.max_total_steps > 0 and self.total_attempts >= self.max_total_steps

    def over_time_budget(self) -> bool:
        return self.timeout_s > 0 and (time.time() - self.started_at) >= self.timeout_s

    def remaining_s(self) -> float:
        return max(0.0, self.timeout_s - (time.time() - self.started_at))

    def snapshot(self) -> dict:
        return {"steps_used": self.total_attempts, "max_steps": self.max_total_steps,
                "elapsed_s": round(time.time() - self.started_at, 1),
                "timeout_s": self.timeout_s}
