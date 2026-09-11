"""The model layer: a local Ollama server, and nothing else.

That is a design constraint, not a limitation waiting to be lifted — Lumen is meant to
run entirely on the machine it is installed on, so there is no code path here that can
send a prompt anywhere else.

Two things this provider does that a thin HTTP wrapper would not:

* it separates *thinking* tokens from *answer* tokens while streaming, so the UI can show
  the model reasoning without that reasoning leaking into the final answer, and
* it recovers a tool call from a model that declares `tools` support but emits the call as
  plain JSON text instead of a native ``tool_calls`` field. Several good local models do
  exactly this (qwen2.5-coder:7b does it on this machine), and treating their output as
  prose would silently turn a working tool call into a hallucinated one.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.errors import NotConfigured

Delta = Callable[[str], None]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResult:
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    latency_ms: int = 0
    native_tools: bool = True     # False when the call was recovered from plain text
    stop_reason: str = ""


class LLMProvider:
    """Interface. Every call in Lumen goes through `chat`."""

    name = "base"
    label = "Base"
    model = ""

    async def chat(self, messages: list[dict], **kwargs: Any) -> LLMResult:
        raise NotImplementedError

    async def healthcheck(self) -> dict:
        raise NotImplementedError

    async def capabilities(self) -> dict:
        raise NotImplementedError

    async def list_models(self) -> dict:
        raise NotImplementedError


class UnconfiguredProvider(LLMProvider):
    """No model picked yet.

    Lumen starts, every screen loads, the MCP library works — and this raises the moment
    something genuinely needs to reason, naming the fix instead of fabricating an answer.
    """

    name = "unconfigured"
    label = "No model selected"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason or (
            "Aucun modèle local n’est sélectionné. Ouvrez Administration → Modèle et "
            "choisissez-en un parmi ceux que votre serveur Ollama a téléchargés."
        )

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResult:
        raise NotConfigured(self.reason)

    async def healthcheck(self) -> dict:
        return {"ok": False, "provider": self.name, "label": self.label, "model": "",
                "error": self.reason}

    async def capabilities(self) -> dict:
        return {"tools": False, "thinking": False, "vision": False, "context_length": 0,
                "source": self.reason}

    async def list_models(self) -> dict:
        return {"ok": False, "models": [], "error": self.reason}


def _explain(exc: Exception, base_url: str) -> str:
    if isinstance(exc, httpx.ConnectError):
        return (f"Ollama est injoignable sur {base_url}. Démarrez-le avec « ollama serve », "
                f"ou pointez LUMEN_OLLAMA_BASE_URL ailleurs.")
    if isinstance(exc, httpx.TimeoutException):
        return f"Ollama sur {base_url} n’a pas répondu à temps."
    return f"{type(exc).__name__}: {exc}"


# A model that ignores the native tool protocol usually still emits the call as a JSON
# object. These are the shapes seen in practice across llama.cpp-family chat templates.
_TOOL_KEYS = ({"name", "arguments"}, {"name", "parameters"}, {"tool", "arguments"},
              {"function", "arguments"}, {"name"}, {"tool_name", "arguments"})


def recover_tool_calls(text: str, known: set[str]) -> tuple[list[ToolCall], str]:
    """Pull tool calls out of a plain-text reply, conservatively.

    Only a payload whose *name matches a tool we actually offered* is accepted — that is
    what keeps an ordinary answer containing JSON from being mistaken for a call. Returns
    (calls, leftover text).
    """
    if not text or not known:
        return [], text
    calls: list[ToolCall] = []
    leftover = text
    # <tool_call>{...}</tool_call>, ```json {...} ```, or a bare top-level object.
    candidates: list[tuple[str, str]] = []
    for match in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.S):
        candidates.append((match.group(0), match.group(1)))
    for match in re.finditer(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", text, re.S):
        candidates.append((match.group(0), match.group(1)))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append((stripped, stripped))
    for whole, payload in candidates:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if not any(set(obj) >= keys for keys in _TOOL_KEYS):
            continue
        name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
        if isinstance(name, dict):
            name = name.get("name")
        if not isinstance(name, str) or name not in known:
            continue
        args = obj.get("arguments") or obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(id=f"rec_{len(calls)}", name=name, arguments=args))
        leftover = leftover.replace(whole, "", 1)
    return calls, leftover.strip()


class OllamaProvider(LLMProvider):
    """A local model served by Ollama. Nothing leaves the machine."""

    name = "ollama"
    label = "Ollama (local)"

    # Ollama's own default context is 4096 tokens regardless of what the model supports.
    # An agent's prompt — system instructions plus two dozen tool schemas — is most of that
    # on its own, so the model gets cut off mid-thought and returns nothing at all, over and
    # over, until the loop's budget is gone. Sizing the window from what the model actually
    # declares is not a tuning preference; it is the difference between working and not.
    # Capped, not maximised. The KV cache for a large window is what pushes a model off
    # the GPU and onto the CPU — on a 9 GB machine this model runs fully on the GPU at
    # 16k and drops to 38% CPU at 32k, which is several times slower for a window nothing
    # here needs. Raise it in Admin when the machine has the memory for it.
    AUTO_CTX_CAP = 16384

    def __init__(self, base_url: str, model: str, timeout_s: int = 900,
                 num_ctx: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx
        self._caps: dict | None = None

    async def context_window(self) -> int:
        """The window to ask Ollama for: what was configured, else the model's own maximum
        capped to something a laptop can hold as KV cache."""
        if self.num_ctx:
            return self.num_ctx
        declared = (await self.capabilities()).get("context_length") or 0
        if not declared:
            return 8192  # nothing declared: still far better than the 4096 default
        return min(declared, self.AUTO_CTX_CAP)

    # --- capability discovery -------------------------------------------------
    async def capabilities(self) -> dict:
        """What this model can actually be handed — asked of the server, never guessed
        from the model's name."""
        if self._caps is not None:
            return self._caps
        declared: list[str] = []
        ctx = 0
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(f"{self.base_url}/api/show", json={"model": self.model})
            if resp.status_code < 400:
                body = resp.json()
                declared = body.get("capabilities") or []
                for key, value in (body.get("model_info") or {}).items():
                    if key.endswith("context_length") and isinstance(value, int):
                        ctx = max(ctx, value)
        except Exception:
            declared = []
        caps = {
            "tools": "tools" in declared,
            "thinking": "thinking" in declared,
            "vision": "vision" in declared,
            "audio": "audio" in declared,
            "context_length": ctx,
            "declared": declared,
            "source": (f"{self.model} déclare : {', '.join(declared)}." if declared
                       else f"{self.model} ne déclare pas ses capacités."),
        }
        self._caps = caps
        return caps

    # --- the one call everything goes through --------------------------------
    async def chat(
        self,
        messages: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 0,
        json_schema: dict | str | None = None,
        think: bool | None = None,
        on_text: Delta | None = None,
        on_thinking: Delta | None = None,
        images: list[tuple[bytes, str]] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> LLMResult:
        """`should_stop` is polled per streamed chunk.

        Without it, Stop does nothing visible until the current generation finishes — up
        to a minute on a local model, which reads as a broken button rather than a slow
        one. Polling per chunk makes it immediate, and what was already generated is kept.
        """
        caps = await self.capabilities()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        payload["options"]["num_ctx"] = await self.context_window()
        if tools:
            payload["tools"] = tools
        if json_schema is not None:
            payload["format"] = json_schema
        if caps["thinking"] and think is not None:
            # Only sent to a model that declares it: Ollama rejects the field outright on
            # models that do not, and a 400 here would look like the model is broken.
            payload["think"] = think
        if images:
            payload["messages"] = [dict(m) for m in payload["messages"]]
            payload["messages"][-1]["images"] = [base64.b64encode(raw).decode() for raw, _ in images]

        started = time.time()
        text_parts: list[str] = []
        think_parts: list[str] = []
        raw_calls: list[dict] = []
        tokens_in = tokens_out = 0
        stop_reason = ""

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=15)) as client:
            try:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode(errors="replace")[:500]
                        raise NotConfigured(
                            f"Ollama a refusé la requête (HTTP {resp.status_code}) : {body}")
                    async for line in resp.aiter_lines():
                        if should_stop is not None and should_stop():
                            stop_reason = "cancelled"
                            break
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        message = chunk.get("message") or {}
                        piece = message.get("content")
                        if piece:
                            text_parts.append(piece)
                            if on_text is not None:
                                on_text(piece)
                        reasoning = message.get("thinking")
                        if reasoning:
                            think_parts.append(reasoning)
                            if on_thinking is not None:
                                on_thinking(reasoning)
                        for call in message.get("tool_calls") or []:
                            raw_calls.append(call)
                        if chunk.get("done"):
                            tokens_in = chunk.get("prompt_eval_count") or 0
                            tokens_out = chunk.get("eval_count") or 0
                            stop_reason = chunk.get("done_reason") or ""
            except httpx.HTTPError as exc:
                raise NotConfigured(_explain(exc, self.base_url)) from exc

        content = "".join(text_parts)
        calls: list[ToolCall] = []
        for index, call in enumerate(raw_calls):
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(id=call.get("id") or f"call_{index}",
                                  name=fn.get("name") or "",
                                  arguments=args if isinstance(args, dict) else {}))
        native = True
        if not calls and tools:
            known = {(t.get("function") or {}).get("name") for t in tools}
            recovered, leftover = recover_tool_calls(content, {k for k in known if k})
            if recovered:
                calls, content, native = recovered, leftover, False

        return LLMResult(
            content=content,
            thinking="".join(think_parts),
            tool_calls=calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=self.model,
            latency_ms=int((time.time() - started) * 1000),
            native_tools=native,
            stop_reason=stop_reason,
        )

    # --- diagnostics ----------------------------------------------------------
    async def healthcheck(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
            names = [m.get("name", "") for m in (resp.json().get("models") or [])]
        except Exception as exc:
            return {"ok": False, "provider": self.name, "label": self.label,
                    "model": self.model, "error": _explain(exc, self.base_url)}
        present = any(n == self.model or n.startswith(f"{self.model}:") for n in names)
        return {
            "ok": present,
            "provider": self.name,
            "label": self.label,
            "model": self.model,
            "base_url": self.base_url,
            "error": None if present else
                     f"'{self.model}' is not pulled on this Ollama server — run: ollama pull {self.model}",
        }

    async def runtime_status(self) -> dict:
        """Whether the model is actually running on the GPU right now.

        Ollama loads what fits and silently runs the rest on the CPU. The difference is
        several times the speed, it is invisible from the outside, and the usual cause —
        a context window larger than the machine's memory can hold as KV cache — is one
        setting away. Surfacing it turns "why is it so slow today" into a number.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/ps")
            entries = resp.json().get("models") or []
        except Exception:
            return {"loaded": False}
        for entry in entries:
            if entry.get("name") == self.model or entry.get("model") == self.model:
                total = entry.get("size") or 0
                vram = entry.get("size_vram") or 0
                return {
                    "loaded": True,
                    "size_gb": round(total / 1e9, 2),
                    "vram_gb": round(vram / 1e9, 2),
                    "gpu_percent": round(100 * vram / total) if total else 0,
                    "context": entry.get("context_length") or 0,
                }
        return {"loaded": False}

    async def list_models(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
        except Exception as exc:
            return {"ok": False, "models": [], "error": _explain(exc, self.base_url)}
        if resp.status_code >= 400:
            return {"ok": False, "models": [],
                    "error": f"HTTP {resp.status_code} depuis {self.base_url}/api/tags — quelque "
                             f"chose écoute à cette adresse, mais ce n’est pas un serveur Ollama."}
        try:
            entries = resp.json().get("models") or []
        except ValueError:
            return {"ok": False, "models": [],
                    "error": f"{self.base_url}/api/tags n’a pas renvoyé de JSON — est-ce bien un "
                             f"serveur Ollama ?"}
        models = []
        for entry in entries:
            name = str(entry.get("name") or "")
            if not name:
                continue
            size = entry.get("size") or 0
            details = entry.get("details") or {}
            remote = name.endswith("-cloud") or name.endswith(":cloud")
            models.append({
                "name": name,
                "size_gb": round(size / 1e9, 1) if size >= 10 ** 8 else 0.0,
                "family": details.get("family") or "",
                "parameters": details.get("parameter_size") or "",
                "local": not remote,
            })
        models.sort(key=lambda m: (not m["local"], m["name"]))
        return {"ok": True, "models": models, "error": None}


async def describe_model(base_url: str, model: str) -> dict:
    """Capabilities of a model that is not the active one — used by the Admin picker so a
    user can see what a model supports *before* selecting it."""
    return await OllamaProvider(base_url, model).capabilities()


def make_provider(base_url: str, model: str, timeout_s: int, num_ctx: int) -> LLMProvider:
    if not base_url or not model:
        return UnconfiguredProvider()
    return OllamaProvider(base_url, model, timeout_s, num_ctx)
