"""The model layer: a local Ollama server, and nothing else.

That is a design constraint, not a limitation waiting to be lifted — Agent is meant to
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

import asyncio
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
    error: str = ""               # what the server said went wrong mid-stream, if anything


class LLMProvider:
    """Interface. Every call in Agent goes through `chat`."""

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

    Agent starts, every screen loads, the MCP library works — and this raises the moment
    something genuinely needs to reason, naming the fix instead of fabricating an answer.
    """

    name = "unconfigured"
    label = "No model selected"

    def __init__(self, reason: str = "") -> None:
        self.reason = reason or (
            "No model selected yet. Open Admin → Model and pick one of the models your "
            "Ollama server offers."
        )

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResult:
        raise NotConfigured(self.reason)

    async def healthcheck(self) -> dict:
        return {"ok": False, "provider": self.name, "label": self.label, "model": "",
                "local": True, "error": self.reason}

    async def capabilities(self) -> dict:
        return {"tools": False, "thinking": False, "vision": False, "context_length": 0,
                "source": self.reason}

    async def list_models(self) -> dict:
        return {"ok": False, "models": [], "error": self.reason}


def runs_remotely(model: str) -> bool:
    """Whether a model tag is hosted by Ollama rather than by this machine.

    The tag is the only honest signal: a cloud model stores nothing locally and reports a
    placeholder size, so nothing else about it distinguishes it from one that does. This
    decides what the interface claims about where a conversation goes, which is not a
    claim worth guessing at.
    """
    name = (model or "").lower()
    return name.endswith("-cloud") or name.endswith(":cloud")


def _explain(exc: Exception, base_url: str, kind: str = "ollama") -> str:
    if kind == "openai":
        if isinstance(exc, httpx.ConnectError):
            return (f"Cannot reach the model server at {base_url}. Is it running, and is this its "
                    f"OpenAI-compatible base URL (usually ending in /v1)?")
        if isinstance(exc, httpx.TimeoutException):
            return f"The model server at {base_url} did not answer in time."
        return f"{type(exc).__name__}: {exc}"
    if isinstance(exc, httpx.ConnectError):
        return (f"Cannot reach Ollama at {base_url}. Start it with `ollama serve`, or point "
                f"AGENT_OLLAMA_BASE_URL somewhere else.")
    if isinstance(exc, httpx.TimeoutException):
        return f"Ollama at {base_url} did not answer in time."
    return f"{type(exc).__name__}: {exc}"


# A model that ignores the native tool protocol usually still emits the call as a JSON
# object. These are the shapes seen in practice across llama.cpp-family chat templates.
_TOOL_KEYS = ({"name", "arguments"}, {"name", "parameters"}, {"tool", "arguments"},
              {"function", "arguments"}, {"name"}, {"tool_name", "arguments"})


_TAG_DEBRIS = re.compile(r"^\s*(?:<\s*/?\s*(?:function|tool_call|parameter)\s*=?\s*)+|(?:\s*<\s*/?\s*(?:function|tool_call|parameter)\w*\s*>?)+\s*$", re.I)
_NAME_WITH_ARGS = re.compile(r"^([A-Za-z_][\w.\-]*)\s*(?:\((.*)\)|(\{.*\}))\s*$", re.S)


def repair_call(name: str, arguments: dict) -> tuple[str, dict]:
    """A call whose arguments ended up inside its name, put back together.

    Small models, and chat templates that speak XML, sometimes emit
    `get_var({"date": "2026-04-30"})</parameter` as the *name* of the tool, with no
    arguments: refused as an unknown tool, the right call is lost to a formatting slip.
    """
    cleaned = _TAG_DEBRIS.sub("", name or "").strip()
    match = _NAME_WITH_ARGS.match(cleaned)
    if not match:
        return cleaned or name, arguments
    tool, inner = match.group(1), (match.group(2) or match.group(3) or "").strip()
    if not inner:
        return tool, arguments
    try:
        parsed = json.loads(inner)
    except ValueError:
        return tool, arguments
    if not isinstance(parsed, dict):
        return tool, arguments
    # Arguments that are only another call's debris — keyed by a tool name — lose to the
    # ones the name carried; real arguments win.
    debris = all(key == tool or "__" in key for key in arguments)
    return tool, parsed if (not arguments or debris) else arguments


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


class _Retryable(Exception):
    """A server-side failure that a second attempt may well survive."""


class OllamaProvider(LLMProvider):
    """A model served through Ollama — running on this machine, or hosted by Ollama when
    the tag says so. :func:`runs_remotely` is what the rest of the app asks to know which."""

    name = "ollama"
    label = "Ollama (local)"

    # Ollama's own default context is 4096 tokens regardless of what the model supports.
    # An agent's prompt — system instructions plus two dozen tool schemas — is most of that
    # on its own, so the model gets cut off mid-thought and returns nothing at all, over and
    # over, until the loop's budget is gone. Sizing the window from what the model actually
    # declares is not a tuning preference; it is the difference between working and not.
    # Capped, not maximised — but only where the cap buys something. On a model running
    # here, the KV cache for a large window is what pushes it off the GPU and onto the
    # CPU: measured on a 9 GB machine, 74% GPU at 16k against 62% at 32k, several times
    # slower for a window nothing here needs. A model Ollama hosts has none of that
    # constraint on this machine, so it gets a window sized for the work instead of for
    # the hardware. Both are overridable in Admin.
    AUTO_CTX_CAP = 16384
    AUTO_CTX_CAP_REMOTE = 65536

    def __init__(self, base_url: str, model: str, timeout_s: int = 900,
                 num_ctx: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx
        self._caps: dict | None = None
        self._last_meta: tuple[int, int, str] = (0, 0, "")

    async def context_window(self) -> int:
        """The window to ask Ollama for: what was configured, else the model's own maximum
        capped to something a laptop can hold as KV cache."""
        if self.num_ctx:
            return self.num_ctx
        declared = (await self.capabilities()).get("context_length") or 0
        if not declared:
            return 8192  # nothing declared: still far better than the 4096 default
        cap = self.AUTO_CTX_CAP_REMOTE if runs_remotely(self.model) else self.AUTO_CTX_CAP
        return min(declared, cap)

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
            "source": (f"{self.model} reports: {', '.join(declared)}." if declared
                       else f"{self.model} does not report its capabilities."),
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

        # One retry, and only on a server-side failure that happened *before* a single
        # token reached the caller. A hosted endpoint returns the occasional 500, and
        # losing a run to one is avoidable; resuming a half-streamed answer is not — the
        # deltas are already on the user's screen, so a second attempt would duplicate
        # them. That condition is the whole safety of this loop.
        stream_errors: list[str] = []
        # Three attempts, backing off: a hosted endpoint's 500s come in short bursts, and
        # one retry a second later often lands in the same burst. Thinking already streamed
        # does not block a retry — it is shown folded, and a repeated paragraph there costs
        # less than a lost run. Answer text or a tool call does.
        delays = (2.0, 5.0, 12.0)
        for attempt in range(len(delays) + 1):
            last = attempt == len(delays)
            try:
                await self._stream_once(payload, text_parts, think_parts, raw_calls,
                                        on_text, on_thinking, should_stop, stream_errors)
                # An error line with nothing shown to the reader yet is as retryable as a
                # 500: nothing would be duplicated by asking again.
                if stream_errors and not last and not (text_parts or raw_calls):
                    think_parts.clear()
                    await asyncio.sleep(delays[attempt])
                    continue
            except _Retryable as exc:
                if not last and not (text_parts or raw_calls):
                    think_parts.clear()
                    await asyncio.sleep(delays[attempt])
                    continue
                raise NotConfigured(str(exc)) from exc
            except httpx.HTTPError as exc:
                if not last and not (text_parts or raw_calls):
                    think_parts.clear()
                    await asyncio.sleep(delays[attempt])
                    continue
                raise NotConfigured(_explain(exc, self.base_url)) from exc
            break
        tokens_in, tokens_out, stop_reason = self._last_meta

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
            name, args = repair_call(fn.get("name") or "", args if isinstance(args, dict) else {})
            calls.append(ToolCall(id=call.get("id") or f"call_{index}", name=name, arguments=args))
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
            error=" | ".join(dict.fromkeys(stream_errors))[:600],
        )

    async def _stream_once(self, payload: dict, text_parts: list[str], think_parts: list[str],
                           raw_calls: list[dict], on_text: Delta | None,
                           on_thinking: Delta | None,
                           should_stop: Callable[[], bool] | None = None,
                           errors: list[str] | None = None) -> None:
        """One pass over the streamed response, appending into the caller's buffers."""
        tokens_in = tokens_out = 0
        stop_reason = ""
        errors = errors if errors is not None else []
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=15)) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:500]
                    message = f"Ollama refused the request (HTTP {resp.status_code}): {body}"
                    # A 4xx is our fault and will fail identically next time; a 5xx is
                    # theirs and often will not.
                    raise (_Retryable(message) if resp.status_code >= 500
                           else NotConfigured(message))
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
                    if chunk.get("error"):
                        # Ollama reports a failure that happens mid-generation as a line of
                        # its own, not as an HTTP status — a tool call it could not parse,
                        # an upstream model error. Skipping it turned a real failure into a
                        # silent, empty turn.
                        errors.append(str(chunk["error"])[:500])
                        continue
                    message_obj = chunk.get("message") or {}
                    piece = message_obj.get("content")
                    if piece:
                        text_parts.append(piece)
                        if on_text is not None:
                            on_text(piece)
                    reasoning = message_obj.get("thinking")
                    if reasoning:
                        think_parts.append(reasoning)
                        if on_thinking is not None:
                            on_thinking(reasoning)
                    for call in message_obj.get("tool_calls") or []:
                        raw_calls.append(call)
                    if chunk.get("done"):
                        tokens_in = chunk.get("prompt_eval_count") or 0
                        tokens_out = chunk.get("eval_count") or 0
                        stop_reason = chunk.get("done_reason") or ""
        self._last_meta = (tokens_in, tokens_out, stop_reason)

    # --- diagnostics ----------------------------------------------------------
    async def healthcheck(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
            names = [m.get("name", "") for m in (resp.json().get("models") or [])]
        except Exception as exc:
            return {"ok": False, "provider": self.name, "label": self.label,
                    "model": self.model, "local": not runs_remotely(self.model),
                    "error": _explain(exc, self.base_url)}
        present = any(n == self.model or n.startswith(f"{self.model}:") for n in names)
        return {
            "ok": present,
            "provider": self.name,
            "label": self.label,
            "model": self.model,
            "local": not runs_remotely(self.model),
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
                    "error": f"HTTP {resp.status_code} from {self.base_url}/api/tags — something "
                             f"is listening there, but it is not an Ollama server."}
        try:
            entries = resp.json().get("models") or []
        except ValueError:
            return {"ok": False, "models": [],
                    "error": f"{self.base_url}/api/tags did not return JSON — is that an "
                             f"Ollama server?"}
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


class OpenAIProvider(LLMProvider):
    """Any server that speaks the OpenAI chat-completions API: vLLM, LM Studio, llama.cpp's
    server, LocalAI, TGI, Ollama's own /v1 — on this machine or inside the network.

    The rest of the app talks in one message format (Ollama's: tool calls without ids,
    tool results by name). This class translates at the edge: ids are given to every tool
    call and threaded to the results that answer them, arguments become JSON strings,
    images become content parts. What differs between servers — structured output, usage
    in the stream — is tried, and dropped once if the server refuses it.
    """

    name = "openai"
    label = "OpenAI-compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout_s: int = 900,
                 num_ctx: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx
        self._caps: dict | None = None
        self._dropped: set[str] = set()     # optional fields this server has refused

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _models(self) -> tuple[list[dict], str]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
        except Exception as exc:
            return [], _explain(exc, self.base_url, "openai")
        if resp.status_code in (401, 403):
            return [], f"HTTP {resp.status_code} from {self.base_url}/models — the API key is missing or refused."
        if resp.status_code >= 400:
            return [], (f"HTTP {resp.status_code} from {self.base_url}/models — is this the OpenAI-compatible "
                        f"base URL (it usually ends in /v1)?")
        try:
            body = resp.json()
        except ValueError:
            return [], f"{self.base_url}/models did not return JSON."
        entries = body.get("data") if isinstance(body, dict) else body
        return [e for e in entries or [] if isinstance(e, dict)], ""

    @staticmethod
    def _context_of(entry: dict) -> int:
        for key in ("max_model_len", "context_length", "context_window", "max_context_length", "n_ctx"):
            value = entry.get(key)
            if isinstance(value, int) and value > 0:
                return value
        meta = entry.get("meta") or {}
        for key in ("n_ctx_train", "n_ctx", "context_length"):
            if isinstance(meta.get(key), int) and meta[key] > 0:
                return meta[key]
        return 0

    async def context_window(self) -> int:
        if self.num_ctx:
            return self.num_ctx
        return (await self.capabilities()).get("context_length") or 8192

    async def capabilities(self) -> dict:
        """What the server says about the model — little, in this API: the context window
        at best. Tool calling is assumed and verified by Admin → Test connection."""
        if self._caps is not None:
            return self._caps
        entries, _error = await self._models()
        entry = next((e for e in entries if e.get("id") == self.model), {})
        name = self.model.lower()
        # An embedding model shares the listing with the chat models and answers no chat.
        embedding = bool(re.search(r"(embed|bge-|e5-|gte-|minilm|nomic-embed|rerank)", name))
        caps = {
            "tools": not embedding,
            "thinking": bool(re.search(r"(r1|qwq|reason|think|gpt-oss|deepseek)", name)),
            "vision": bool(re.search(r"(vision|vl\b|-vl|llava|pixtral|gemma-3|gemma3|minicpm-v)", name)),
            "audio": False,
            "context_length": self._context_of(entry),
            "declared": [],
            "source": (f"{self.model} on an OpenAI-compatible server: capabilities are not declared "
                       f"by this API; tool calling is assumed — Admin → Test connection verifies it."),
        }
        self._caps = caps
        return caps

    # --- messages, translated at the edge ------------------------------------
    @staticmethod
    def _convert(messages: list[dict], system: str) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}] if system else []
        pending: list[str] = []
        counter = 0
        for message in messages:
            role = message.get("role")
            if role == "assistant" and message.get("tool_calls"):
                calls = []
                for call in message["tool_calls"]:
                    fn = call.get("function") or {}
                    counter += 1
                    call_id = str(call.get("id") or f"call_{counter}")
                    args = fn.get("arguments")
                    calls.append({"id": call_id, "type": "function",
                                  "function": {"name": fn.get("name") or "",
                                               "arguments": args if isinstance(args, str)
                                               else json.dumps(args or {}, ensure_ascii=False)}})
                    pending.append(call_id)
                out.append({"role": "assistant", "content": message.get("content") or None, "tool_calls": calls})
                continue
            if role == "tool":
                if pending:
                    call_id = str(message.get("tool_call_id") or "")
                    if call_id in pending:
                        pending.remove(call_id)
                    else:
                        call_id = pending.pop(0)
                    out.append({"role": "tool", "tool_call_id": call_id, "content": str(message.get("content") or "")})
                else:
                    # A result whose call was recovered from text has no call to answer:
                    # it is handed over as what it is, a message with the result.
                    out.append({"role": "user", "content": f"[Result of {message.get('name') or 'a tool'}]\n"
                                                           f"{message.get('content') or ''}"})
                continue
            entry = {"role": role if role in ("user", "assistant", "system") else "user",
                     "content": message.get("content") or ""}
            if message.get("images"):
                parts = [{"type": "text", "text": entry["content"]}]
                parts += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}}
                          for image in message["images"]]
                entry["content"] = parts
            out.append(entry)
        return out

    async def chat(self, messages: list[dict], *, system: str = "", tools: list[dict] | None = None,
                   temperature: float = 0.3, max_tokens: int = 0, json_schema: dict | str | None = None,
                   think: bool | None = None, on_text: Delta | None = None,
                   on_thinking: Delta | None = None, images: list[tuple[bytes, str]] | None = None,
                   should_stop: Callable[[], bool] | None = None) -> LLMResult:
        started = time.time()
        converted = [dict(m) for m in messages]
        if images and converted:
            converted[-1]["images"] = [base64.b64encode(raw).decode() for raw, _ in images]
        payload: dict[str, Any] = {"model": self.model, "messages": self._convert(converted, system),
                                   "stream": True, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_schema is not None and "response_format" not in self._dropped:
            payload["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": "response", "schema": json_schema}}
                if isinstance(json_schema, dict) else {"type": "json_object"})
        if "stream_options" not in self._dropped:
            payload["stream_options"] = {"include_usage": True}

        delays = (2.0, 5.0, 12.0)
        text_parts: list[str] = []
        think_parts: list[str] = []
        calls: dict[int, dict] = {}
        meta = {"in": 0, "out": 0, "stop": "", "errors": []}
        for attempt in range(len(delays) + 1):
            last = attempt == len(delays)
            try:
                await self._stream_once(payload, text_parts, think_parts, calls, meta,
                                        on_text, on_thinking, should_stop)
            except _Refused as exc:
                # An optional field the server does not know: drop it once, ask again.
                optional = [k for k in ("response_format", "stream_options", "tool_choice") if k in payload]
                if optional and not (text_parts or calls):
                    for key in optional:
                        payload.pop(key, None)
                        self._dropped.add(key)
                    continue
                raise NotConfigured(str(exc)) from exc
            except _Retryable as exc:
                if not last and not (text_parts or calls):
                    think_parts.clear()
                    await asyncio.sleep(delays[attempt])
                    continue
                raise NotConfigured(str(exc)) from exc
            except httpx.HTTPError as exc:
                if not last and not (text_parts or calls):
                    think_parts.clear()
                    await asyncio.sleep(delays[attempt])
                    continue
                raise NotConfigured(_explain(exc, self.base_url, "openai")) from exc
            break

        content = "".join(text_parts)
        tool_calls: list[ToolCall] = []
        for index in sorted(calls):
            raw = calls[index]
            try:
                args = json.loads(raw["arguments"]) if raw["arguments"].strip() else {}
            except json.JSONDecodeError:
                args = {}
            if raw["name"]:
                name, args = repair_call(raw["name"], args if isinstance(args, dict) else {})
                tool_calls.append(ToolCall(id=raw["id"] or f"call_{index}", name=name, arguments=args))
        native = True
        if not tool_calls and tools:
            known = {(t.get("function") or {}).get("name") for t in tools}
            recovered, leftover = recover_tool_calls(content, {k for k in known if k})
            if recovered:
                tool_calls, content, native = recovered, leftover, False
        return LLMResult(content=content, thinking="".join(think_parts), tool_calls=tool_calls,
                         tokens_in=meta["in"], tokens_out=meta["out"], model=self.model,
                         latency_ms=int((time.time() - started) * 1000), native_tools=native,
                         stop_reason=meta["stop"], error=" | ".join(dict.fromkeys(meta["errors"]))[:600])

    async def _stream_once(self, payload: dict, text_parts: list[str], think_parts: list[str],
                           calls: dict[int, dict], meta: dict, on_text: Delta | None,
                           on_thinking: Delta | None, should_stop: Callable[[], bool] | None) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=15)) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload,
                                     headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:500]
                    message = f"The model server refused the request (HTTP {resp.status_code}): {body}"
                    if resp.status_code >= 500:
                        raise _Retryable(message)
                    if resp.status_code in (400, 422):
                        raise _Refused(message)
                    raise NotConfigured(message)
                async for line in resp.aiter_lines():
                    if should_stop is not None and should_stop():
                        meta["stop"] = "cancelled"
                        break
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        meta["errors"].append(str(chunk["error"])[:500])
                        continue
                    usage = chunk.get("usage") or {}
                    if usage:
                        meta["in"] = usage.get("prompt_tokens") or meta["in"]
                        meta["out"] = usage.get("completion_tokens") or meta["out"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or choice.get("message") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            if on_text is not None:
                                on_text(piece)
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning:
                            think_parts.append(reasoning)
                            if on_thinking is not None:
                                on_thinking(reasoning)
                        for call in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(call.get("index") or 0), {"id": "", "name": "", "arguments": ""})
                            if call.get("id"):
                                slot["id"] = call["id"]
                            fn = call.get("function") or {}
                            if fn.get("name") and not slot["name"].endswith(fn["name"]):
                                slot["name"] += fn["name"]
                            arguments = fn.get("arguments")
                            if isinstance(arguments, dict):
                                slot["arguments"] = json.dumps(arguments)
                            elif arguments:
                                slot["arguments"] += arguments
                        if choice.get("finish_reason"):
                            meta["stop"] = choice["finish_reason"]

    async def healthcheck(self) -> dict:
        entries, error = await self._models()
        base = {"provider": self.name, "label": self.label, "model": self.model, "local": True,
                "base_url": self.base_url}
        if error:
            return {**base, "ok": False, "error": error}
        ids = [e.get("id") for e in entries]
        present = not ids or self.model in ids
        return {**base, "ok": present,
                "error": None if present else f"'{self.model}' is not served here. Served: {', '.join(map(str, ids[:8]))}."}

    async def runtime_status(self) -> dict:
        return {"loaded": False}

    async def list_models(self) -> dict:
        entries, error = await self._models()
        if error:
            return {"ok": False, "models": [], "error": error}
        models = [{"name": str(e.get("id")), "size_gb": 0.0, "family": str(e.get("owned_by") or ""),
                   "parameters": "", "local": True, "context_length": self._context_of(e)}
                  for e in entries if e.get("id")]
        models.sort(key=lambda m: m["name"])
        return {"ok": True, "models": models, "error": None}


class _Refused(Exception):
    """A 4xx the request might not get if it asked for less."""


async def describe_model(base_url: str, model: str) -> dict:
    """Capabilities of a model that is not the active one — used by the Admin picker so a
    user can see what a model supports *before* selecting it."""
    return await OllamaProvider(base_url, model).capabilities()


def make_provider(base_url: str, model: str, timeout_s: int, num_ctx: int,
                  kind: str = "ollama", api_key: str = "") -> LLMProvider:
    if not base_url or not model:
        return UnconfiguredProvider()
    if kind == "openai":
        return OpenAIProvider(base_url, model, api_key, timeout_s, num_ctx)
    return OllamaProvider(base_url, model, timeout_s, num_ctx)


async def probe_connection(kind: str, base_url: str, api_key: str = "", model: str = "") -> dict:
    """Admin → Test connection: can the server be reached, which models does it serve, does
    the model answer, and does it call a tool when offered one. Each step timed."""
    report: dict[str, Any] = {"provider": kind, "base_url": base_url.rstrip("/")}
    lister = (OpenAIProvider(base_url, model, api_key) if kind == "openai" else OllamaProvider(base_url, model))
    started = time.time()
    listing = await lister.list_models()
    report["reachable"] = bool(listing.get("ok"))
    report["list_ms"] = int((time.time() - started) * 1000)
    report["models"] = [m["name"] for m in listing.get("models") or []]
    report["error"] = listing.get("error")
    if not listing.get("ok"):
        return report
    model = model or (report["models"][0] if report["models"] else "")
    report["model"] = model
    if not model:
        report["chat"] = {"ok": False, "error": "The server serves no model."}
        return report
    provider = make_provider(base_url, model, 120, 0, kind, api_key)
    try:
        started = time.time()
        # Room for a reasoning model to think before it writes the word.
        result = await asyncio.wait_for(provider.chat(
            [{"role": "user", "content": "Reply with exactly the word OK."}], temperature=0.0, max_tokens=600), 180)
        reply = result.content.strip()
        report["chat"] = {"ok": bool(reply or result.thinking.strip()),
                          "reply": reply[:80] or ("(reasoned, no final text within the limit)" if result.thinking else ""),
                          "thinking": bool(result.thinking.strip()),
                          "ms": int((time.time() - started) * 1000),
                          "tokens_out": result.tokens_out}
    except Exception as exc:  # noqa: BLE001
        report["chat"] = {"ok": False, "error": str(exc)[:400]}
        return report
    tool = {"type": "function", "function": {
        "name": "get_time", "description": "Returns the current time in a time zone.",
        "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}, "required": ["timezone"]}}}
    try:
        started = time.time()
        result = await asyncio.wait_for(provider.chat(
            [{"role": "user", "content": "What time is it in Paris? Use the tool."}], tools=[tool],
            temperature=0.0, max_tokens=600), 180)
        called = [c for c in result.tool_calls if c.name == "get_time"]
        report["tools"] = {"ok": bool(called), "native": result.native_tools,
                           "arguments": called[0].arguments if called else {},
                           "ms": int((time.time() - started) * 1000),
                           "error": None if called else "The model answered without calling the tool: "
                                                        "the agent needs tool calling."}
    except Exception as exc:  # noqa: BLE001
        report["tools"] = {"ok": False, "error": str(exc)[:400]}
    return report
