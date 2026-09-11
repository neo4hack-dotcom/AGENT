"""Long-term memory: facts the agent chose to keep, recalled by overlap.

Deliberately not a vector store. Embeddings would mean a second model to pull, keep
loaded and keep in sync with the store — for the handful of durable facts a personal
agent accumulates, weighted term overlap retrieves them just as well and stays
inspectable: every entry is readable text in the same JSON file as everything else.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.store import new_id, now

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "for", "on",
        "with", "that", "this", "it", "as", "at", "by", "be", "from", "les", "des", "une",
        "un", "le", "la", "et", "de", "du", "au", "aux", "que", "qui", "pour", "dans",
        "est", "sont", "sur", "avec", "par", "ce", "se", "ne", "pas", "plus"}


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zà-ÿ0-9_]{2,}", (text or "").lower()) if t not in STOP]


class Memory:
    def __init__(self, store) -> None:
        self.store = store

    def all(self) -> list[dict]:
        return self.store.memory()

    def add(self, text: str, *, source: str = "agent", conversation_id: str = "") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Nothing to remember.")
        entries = self.store.memory()
        # Near-duplicates are updated in place rather than piling up: a fact restated in
        # three conversations should be one memory, not three.
        incoming = set(tokens(text))
        for entry in entries:
            existing = set(tokens(entry["text"]))
            if existing and incoming:
                overlap = len(existing & incoming) / len(existing | incoming)
                if overlap > 0.75:
                    entry.update({"text": text, "updated_at": now(),
                                  "hits": entry.get("hits", 0)})
                    self.store.touch()
                    return entry
        entry = {"id": new_id("m"), "text": text, "source": source,
                 "conversation_id": conversation_id, "created_at": now(),
                 "updated_at": now(), "hits": 0}
        entries.append(entry)
        self.store.touch()
        return entry

    def forget(self, memory_id: str) -> bool:
        entries = self.store.memory()
        for index, entry in enumerate(entries):
            if entry["id"] == memory_id:
                entries.pop(index)
                self.store.touch()
                return True
        return False

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        entries = self.store.memory()
        if not entries:
            return []
        query_terms = Counter(tokens(query))
        if not query_terms:
            return []
        # Rarer terms carry more signal, so weight by inverse document frequency.
        doc_freq: Counter = Counter()
        tokenised = []
        for entry in entries:
            terms = set(tokens(entry["text"]))
            tokenised.append(terms)
            doc_freq.update(terms)
        total = len(entries)
        scored = []
        for entry, terms in zip(entries, tokenised):
            score = sum(
                count * math.log(1 + total / (1 + doc_freq[term]))
                for term, count in query_terms.items() if term in terms
            )
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = [entry for _, entry in scored[:limit]]
        for entry in top:
            entry["hits"] = entry.get("hits", 0) + 1
        if top:
            self.store.touch()
        return top

    def prompt_block(self, query: str, limit: int = 5) -> str:
        found = self.recall(query, limit)
        if not found:
            return ""
        lines = "\n".join(f"- {entry['text']}" for entry in found)
        return f"\n\nThings you remembered about this user from earlier conversations:\n{lines}"
