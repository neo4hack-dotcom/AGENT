"""The agent loop.

One user message becomes one *run*: a bounded think → act → observe cycle that ends with
an answer grounded in what the tools actually returned.

The shape is deliberate. A nine-node plan/execute/critique state machine costs nine LLM
calls before a word reaches the user — on a local 4B model that is a minute of silence to
answer "hello". So the loop is the fast path, and the rigour is attached where it earns
its latency:

* the **loop guard** runs on every call, free, and is what makes the loop bounded;
* the **critic** runs only on a *failed* call, where its advice changes the next attempt;
* the **reflection gap-check** runs once, only on runs that actually used several tools,
  where "every step succeeded but the whole thing missed the point" is a real risk;
* the **plan** is a first-class artifact the model maintains through a tool, so it is
  visible and editable rather than buried in a prompt.

What the user sees is the run's event stream; what is persisted is the same thing, as
blocks, so reopening a conversation shows exactly what happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from app.agent import builtin, prompts
from app.agent.guard import LoopGuard, signature
from app.errors import NotConfigured, RunCancelled
from app.llm.provider import ToolCall
from app.store import new_id, now

APPROVAL_TIMEOUT_S = 600


class RunContext:
    """Everything one run needs, and the only place its mutable state lives."""

    def __init__(self, run_id: str, conversation_id: str, message_id: str, bus,
                 guard: LoopGuard) -> None:
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.bus = bus
        self.guard = guard
        self.topic = f"run:{run_id}"
        self.blocks: list[dict] = []
        self.plan: list[dict] = []
        self.usage = {"llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "tool_calls": 0}
        self.cancelled = False
        self.status = "running"
        self.error: str | None = None
        self.buffer: list[dict] = []     # replayed to a client that reconnects mid-run
        # Identical (tool, arguments) inside one run returns the first result instead of
        # doing the work twice. A model that re-fetches the same page mid-run is not
        # gathering new evidence, it is stalling — and each repeat costs a full turn.
        self.tool_cache: dict[str, dict] = {}
        self._approvals: dict[str, asyncio.Future] = {}
        self._finished = asyncio.Event()

    def supersede_text(self) -> None:
        """Mark the draft answer as replaced.

        When the loop continues after the model has already written prose — a gap check
        sent it back for more evidence, say — that prose is a draft, not the answer.
        Persisting it alongside the real answer is how a run ends up saying the same
        thing twice, in two different moods.
        """
        for block in self.blocks:
            if block["type"] == "text" and not block.get("superseded"):
                block["superseded"] = True
                self.emit({"type": "block.supersede", "index": block["index"]})

    def has_answer(self) -> bool:
        return any(b["type"] == "text" and not b.get("superseded") and (b.get("text") or "").strip()
                   for b in self.blocks)

    def emit(self, event: dict) -> None:
        event = {**event, "run_id": self.run_id, "seq": len(self.buffer)}
        self.buffer.append(event)
        if len(self.buffer) > 4000:
            # A very long run keeps its tail; the persisted blocks remain the full record.
            del self.buffer[:1000]
        self.bus.emit(self.topic, event)

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelled("Stopped.")

    # --- human-in-the-loop ----------------------------------------------------
    async def request_approval(self, call_id: str, payload: dict) -> bool:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approvals[call_id] = future
        self.emit({"type": "approval.request", "call_id": call_id, **payload})
        try:
            return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.emit({"type": "approval.resolved", "call_id": call_id, "approved": False,
                       "reason": "timed out"})
            return False
        finally:
            self._approvals.pop(call_id, None)

    def resolve_approval(self, call_id: str, approved: bool) -> bool:
        future = self._approvals.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        self.emit({"type": "approval.resolved", "call_id": call_id, "approved": approved})
        return True

    def cancel(self) -> None:
        self.cancelled = True
        for future in self._approvals.values():
            if not future.done():
                future.set_result(False)

    def finish(self) -> None:
        self._finished.set()

    async def wait_finished(self, timeout: float | None = None) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._finished.wait(), timeout=timeout)


class AgentRunner:
    def __init__(self, container) -> None:
        self.c = container
        self.runs: dict[str, RunContext] = {}

    # ------------------------------------------------------------------ public
    def get(self, run_id: str) -> RunContext | None:
        return self.runs.get(run_id)

    def active_for(self, conversation_id: str) -> str:
        """The run still working on this conversation, if any.

        This is what lets a reload re-attach to an answer in progress instead of showing a
        message frozen mid-sentence — the run never depended on the browser being there,
        so the browser should be able to come back to it.
        """
        for run_id, ctx in self.runs.items():
            if ctx.conversation_id == conversation_id and ctx.status == "running":
                return run_id
        return ""

    async def start(self, conversation_id: str, text: str, images: list[dict] | None = None) -> dict:
        """Create the run, kick it off in the background, and return its identifiers.

        The run lives past the HTTP request on purpose: closing the tab, or losing the
        connection mid-answer, must not throw away work the model already did.
        """
        conv = self.c.store.conversation(conversation_id)
        if conv is None:
            conv = self.c.store.create_conversation()
            conversation_id = conv["id"]

        user_message = {
            "id": new_id("m"), "role": "user", "content": text,
            "created_at": now(),
            "images": [{"name": i.get("name", ""), "mime": i.get("mime", "")} for i in (images or [])],
        }
        self.c.store.append_message(conversation_id, user_message)

        assistant_message = {
            "id": new_id("m"), "role": "assistant", "content": "", "created_at": now(),
            "blocks": [], "plan": [], "usage": {}, "status": "running", "error": None,
            "model": getattr(self.c.llm, "model", ""),
        }
        self.c.store.append_message(conversation_id, assistant_message)

        guard = LoopGuard(self.c.settings.max_iterations * 3,
                          self.c.settings.run_timeout_s,
                          self.c.settings.stagnation_limit)
        ctx = RunContext(new_id("r"), conversation_id, assistant_message["id"], self.c.bus, guard)
        self.runs[ctx.run_id] = ctx
        asyncio.create_task(self._drive(ctx, text, images or []))
        return {"run_id": ctx.run_id, "conversation_id": conversation_id,
                "user_message_id": user_message["id"], "message_id": assistant_message["id"]}

    def cancel(self, run_id: str) -> bool:
        ctx = self.runs.get(run_id)
        if ctx is None:
            return False
        ctx.cancel()
        return True

    def approve(self, run_id: str, call_id: str, approved: bool) -> bool:
        ctx = self.runs.get(run_id)
        return bool(ctx and ctx.resolve_approval(call_id, approved))

    # ----------------------------------------------------------------- driving
    async def _drive(self, ctx: RunContext, text: str, images: list[dict]) -> None:
        try:
            await self._run(ctx, text, images)
            ctx.status = "completed" if not ctx.error else "failed"
        except RunCancelled:
            ctx.status = "cancelled"
            ctx.emit({"type": "status", "phase": "cancelled"})
        except NotConfigured as exc:
            ctx.status = "failed"
            ctx.error = str(exc)
            ctx.emit({"type": "error", "message": str(exc), "kind": "not_configured"})
        except Exception as exc:  # noqa: BLE001 - the run must always end with a verdict
            ctx.status = "failed"
            ctx.error = f"{type(exc).__name__}: {exc}"
            ctx.emit({"type": "error", "message": ctx.error, "kind": "internal"})
        finally:
            await self._persist(ctx)
            ctx.emit({"type": "done", "status": ctx.status, "usage": ctx.usage,
                      "message_id": ctx.message_id})
            ctx.finish()
            # Kept briefly so a reconnecting client can still replay the tail.
            asyncio.create_task(self._expire(ctx.run_id))

    async def _expire(self, run_id: str, delay: float = 900) -> None:
        await asyncio.sleep(delay)
        self.runs.pop(run_id, None)

    async def _persist(self, ctx: RunContext) -> None:
        conv = self.c.store.conversation(ctx.conversation_id)
        if conv is None:
            return
        for message in conv["messages"]:
            if message["id"] != ctx.message_id:
                continue
            message["blocks"] = ctx.blocks
            message["plan"] = ctx.plan
            message["usage"] = ctx.usage
            message["status"] = ctx.status
            message["error"] = ctx.error
            message["content"] = "\n\n".join(
                b["text"] for b in ctx.blocks
                if b["type"] == "text" and b.get("text") and not b.get("superseded"))
            message["completed_at"] = now()
        self.c.store.touch()
        await self.c.store.save()

    # ------------------------------------------------------------------- prompt
    def _tool_surface(self, ctx: RunContext) -> tuple[dict[str, builtin.ToolSpec], list[dict], list[dict]]:
        def on_plan(steps: list[dict]) -> None:
            ctx.plan = steps
            ctx.emit({"type": "plan", "steps": steps})

        workspace = Path(self.c.settings.workspace_dir).expanduser().resolve()
        tools = builtin.build_registry(self.c.settings, self.c.memory, workspace, on_plan)
        mcp_tools = self.c.mcp.tools()
        functions = [spec.as_function() for spec in tools.values()] + self.c.mcp.ollama_tools()
        return tools, mcp_tools, functions

    def _history(self, conversation_id: str, upto_message_id: str) -> list[dict]:
        """Replay previous turns for the model.

        The most recent assistant turn keeps its tool results verbatim — follow-ups like
        "and the third one?" depend on them. Older turns keep only a one-line trace of
        what was called, which is what stops a long conversation from spending its whole
        context window re-reading pages it already summarised.
        """
        conv = self.c.store.conversation(conversation_id) or {}
        messages = [m for m in conv.get("messages", []) if m["id"] != upto_message_id]
        turns = messages[-(self.c.settings.history_turns * 2):]
        out: list[dict] = []
        assistant_indexes = [i for i, m in enumerate(turns) if m["role"] == "assistant"]
        last_assistant = assistant_indexes[-1] if assistant_indexes else -1
        for index, message in enumerate(turns):
            if message["role"] == "user":
                out.append({"role": "user", "content": message.get("content") or ""})
                continue
            parts: list[str] = []
            verbatim = index == last_assistant
            for block in message.get("blocks") or []:
                if block["type"] == "text" and block.get("text"):
                    parts.append(block["text"])
                elif block["type"] == "tool":
                    mark = "ok" if block.get("ok") else "failed"
                    line = f"[called {block.get('name')} → {mark}: {block.get('summary', '')[:200]}]"
                    if verbatim and block.get("ok") and block.get("text"):
                        line += f"\n{builtin.truncate_for_model(block['text'], 2500)}"
                    parts.append(line)
            content = "\n\n".join(p for p in parts if p) or (message.get("content") or "")
            if content:
                out.append({"role": "assistant", "content": content})
        return out

    # --------------------------------------------------------------- main loop
    async def _run(self, ctx: RunContext, text: str, images: list[dict]) -> None:
        settings = self.c.settings
        ctx.emit({"type": "status", "phase": "starting"})
        # A run launched seconds after boot would otherwise plan around an empty tool
        # surface while servers are still handshaking.
        await self.c.mcp.wait_ready(timeout=min(8.0, settings.mcp_startup_timeout_s))

        tools, mcp_tools, functions = self._tool_surface(ctx)
        catalog = builtin.catalog_text(tools, mcp_tools)
        memory_block = self.c.memory.prompt_block(text)
        system = prompts.system_prompt(getattr(self.c.llm, "model", ""), catalog, memory_block)

        messages = self._history(ctx.conversation_id, ctx.message_id)
        messages.append({"role": "user", "content": text})
        image_payload = [(i["data"], i.get("mime", "image/png")) for i in images if i.get("data")]

        caps = await self.c.llm.capabilities() if hasattr(self.c.llm, "capabilities") else {}
        if image_payload and not caps.get("vision"):
            ctx.emit({"type": "notice",
                      "message": f"{getattr(self.c.llm, 'model', 'Ce modèle')} ne sait pas lire "
                                 f"les images : la pièce jointe n’a pas été envoyée. Choisissez un "
                                 f"modèle avec la capacité « vision » dans l’administration."})
            image_payload = []

        reflected = False
        must_compose = False
        empty_turns = 0
        tools_used = 0
        pending_hint: str | None = None
        # Thinking is worth its latency when choosing a strategy or recovering from a
        # failure, and not much else. On a local model it is most of the wall clock, so
        # it is spent deliberately rather than on every turn.
        deep_think = True

        for iteration in range(settings.max_iterations):
            ctx.check_cancelled()
            if ctx.guard.over_time_budget():
                ctx.emit({"type": "notice", "message":
                          f"Budget de temps atteint ({settings.run_timeout_s} s) — réponse avec "
                          f"ce qui a été réuni jusqu’ici."})
                break

            if pending_hint:
                messages.append({"role": "user", "content": pending_hint})
                pending_hint = None

            last_turn = iteration == settings.max_iterations - 1
            ctx.emit({"type": "status", "phase": "thinking"})
            text_block: dict | None = None
            think_block: dict | None = None

            def on_text(piece: str) -> None:
                nonlocal text_block
                if text_block is None:
                    text_block = {"type": "text", "text": "", "index": len(ctx.blocks)}
                    ctx.blocks.append(text_block)
                    ctx.emit({"type": "block.open", "kind": "text", "index": text_block["index"]})
                text_block["text"] += piece
                ctx.emit({"type": "text.delta", "index": text_block["index"], "text": piece})

            def on_thinking(piece: str) -> None:
                nonlocal think_block
                if think_block is None:
                    think_block = {"type": "thinking", "text": "", "index": len(ctx.blocks)}
                    ctx.blocks.append(think_block)
                    ctx.emit({"type": "block.open", "kind": "thinking", "index": think_block["index"]})
                think_block["text"] += piece
                ctx.emit({"type": "thinking.delta", "index": think_block["index"], "text": piece})

            result = await self.c.llm.chat(
                messages,
                system=system,
                tools=None if last_turn else functions,
                temperature=0.35,
                think=deep_think,
                on_text=on_text,
                on_thinking=on_thinking,
                images=image_payload or None,
                should_stop=lambda: ctx.cancelled,
            )
            ctx.check_cancelled()
            image_payload = []  # attachments belong to the first turn only
            deep_think = False   # re-enabled below only when something actually goes wrong
            ctx.usage["llm_calls"] += 1
            ctx.usage["tokens_in"] += result.tokens_in
            ctx.usage["tokens_out"] += result.tokens_out
            ctx.emit({"type": "usage", **ctx.usage})

            if not result.tool_calls:
                if not (result.content or "").strip() and not last_turn:
                    # An empty turn is a stall, and it has exactly two causes worth telling
                    # apart. Cut off by the context window: nudging is useless, the next turn
                    # will be cut off in the same place. Genuinely blank: one nudge is worth
                    # a try. Either way this is capped — twelve silent retries was the loop
                    # spending its whole budget on a model that could not answer.
                    empty_turns += 1
                    truncated = result.stop_reason == "length"
                    if truncated:
                        window = await self._context_window()
                        ctx.emit({"type": "notice", "message":
                                  f"Le modèle a été coupé net : le prompt occupe {result.tokens_in} "
                                  f"tokens sur une fenêtre de {window}. Réduisez le nombre de "
                                  f"serveurs MCP connectés, ou augmentez la fenêtre de contexte."})
                    if truncated or empty_turns >= 2:
                        must_compose = True
                        break
                    pending_hint = prompts.note(
                        "Your last turn produced nothing at all. Either call a tool or write "
                        "the answer to the user's question now.")
                    deep_think = True
                    continue
                empty_turns = 0
                if tools_used >= settings.critic_min_tools and not reflected and not last_turn:
                    reflected = True
                    gap = await self._reflect(ctx, text, tools)
                    if not gap.get("complete"):
                        if gap.get("tool"):
                            await self._run_gap_step(ctx, gap["tool"], gap["arguments"], tools)
                        # The draft was judged incomplete, so it is a draft: the answer is
                        # composed once, at the end, from every piece of evidence at once.
                        ctx.supersede_text()
                        must_compose = True
                if must_compose:
                    ctx.supersede_text()
                break

            assistant_entry: dict[str, Any] = {"role": "assistant", "content": result.content or ""}
            if result.native_tools:
                assistant_entry["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in result.tool_calls
                ]
            messages.append(assistant_entry)

            outcomes = await self._execute_calls(ctx, result.tool_calls, tools)
            tools_used += len(outcomes)
            for call, outcome in outcomes:
                messages.append({
                    "role": "tool",
                    "tool_name": call.name,
                    "name": call.name,
                    "content": builtin.truncate_for_model(outcome["model_text"]),
                })
            if outcomes:
                # The question, restated after every batch of results. Sixty tokens that
                # stop a small model from answering the page it just read instead of the
                # person who asked — often in the page's language rather than theirs.
                messages.append({"role": "user", "content": prompts.anchor(text)})
                # Prose written before a tool call is commentary on what it is about to
                # do, not the answer — the answer comes after the results are in.
                ctx.supersede_text()
                empty_turns = 0

            failures = [(call, out) for call, out in outcomes if not out["ok"]]
            if failures and not last_turn:
                call, out = failures[0]
                verdict = await self._critique(ctx, call, out)
                deep_think = True
                if verdict["status"] == "give_up":
                    pending_hint = prompts.note(
                        f"`{call.name}` failed and the Critic judges this approach unworkable: "
                        f"{verdict['reason']}. "
                        f"{verdict['advice'] or 'Answer with what you have and state what is missing.'}")
                else:
                    pending_hint = prompts.heal_hint(call.name, out.get("error", ""), verdict["advice"])
        else:
            ctx.emit({"type": "notice",
                      "message": f"Plafond de {settings.max_iterations} tours d’outils atteint — "
                                 f"réponse avec ce qui a été réuni."})

        if not ctx.has_answer():
            # Every run ends with something readable, even a run that only failed.
            await self._final_answer(ctx, text)

        ctx.emit({"type": "status", "phase": "done"})
        asyncio.create_task(self._maybe_title(ctx.conversation_id))

    async def _context_window(self) -> int:
        getter = getattr(self.c.llm, "context_window", None)
        return await getter() if getter else 0

    # --------------------------------------------------------------- tool calls
    async def _execute_calls(self, ctx: RunContext, calls: list[ToolCall],
                             tools: dict[str, builtin.ToolSpec]) -> list[tuple[ToolCall, dict]]:
        settings = self.c.settings
        prepared: list[tuple[ToolCall, dict]] = []
        immediate: list[tuple[ToolCall, dict]] = []

        for call in calls[: settings.parallel_max_fanout]:
            ctx.check_cancelled()
            spec = tools.get(call.name)
            mcp_tool = None if spec else self.c.mcp.resolve(call.name)
            block = {"type": "tool", "index": len(ctx.blocks), "id": call.id or new_id("t"),
                     "name": call.name, "args": call.arguments,
                     "server": (mcp_tool["server_name"] if mcp_tool else
                                (spec.group if spec else "unknown")),
                     "kind": "mcp" if mcp_tool else "builtin",
                     "status": "running", "ok": None, "summary": "", "text": "", "ms": 0}
            ctx.blocks.append(block)
            ctx.emit({"type": "tool.start", "index": block["index"], "id": block["id"],
                      "name": call.name, "args": call.arguments, "server": block["server"],
                      "kind": block["kind"]})

            if spec is None and mcp_tool is None:
                known = sorted(list(tools) + [t["qualified_name"] for t in self.c.mcp.tools()])[:14]
                immediate.append((call, self._fail(ctx, block,
                    f"There is no tool called '{call.name}'. Available: {', '.join(known)}.")))
                continue

            if ctx.guard.is_stagnant(call.name, call.arguments):
                immediate.append((call, self._fail(ctx, block,
                    f"'{call.name}' already failed twice with these exact arguments. Change the "
                    f"arguments or the approach — repeating it will fail again.")))
                continue
            if ctx.guard.repeat_count(call.name, call.arguments) >= 2:
                immediate.append((call, self._fail(ctx, block,
                    f"You already ran '{call.name}' with these exact arguments twice; the result "
                    f"is unchanged and is above in this conversation. Move on.")))
                continue

            if self._needs_approval(spec, mcp_tool):
                block["status"] = "awaiting_approval"
                approved = await ctx.request_approval(block["id"], {
                    "index": block["index"], "name": call.name, "args": call.arguments,
                    "server": block["server"], "kind": block["kind"],
                    "reason": "Cet outil peut modifier quelque chose en dehors de Lumen."})
                if not approved:
                    immediate.append((call, self._fail(ctx, block,
                        "The user declined this call. Do not retry it; continue without it or "
                        "explain what cannot be done.", status="denied")))
                    continue
                block["status"] = "running"

            prepared.append((call, block))

        semaphore = asyncio.Semaphore(settings.parallel_max_fanout)

        async def run_one(call: ToolCall, block: dict) -> tuple[ToolCall, dict]:
            async with semaphore:
                return call, await self._invoke(ctx, call, block, tools)

        results = await asyncio.gather(*(run_one(c, b) for c, b in prepared)) if prepared else []
        return immediate + list(results)

    def _needs_approval(self, spec: builtin.ToolSpec | None, mcp_tool: dict | None) -> bool:
        mode = self.c.prefs().get("approval_mode", "writes")
        if mode == "never":
            return False
        if mcp_tool is not None:
            if mcp_tool.get("auto_approve"):
                return False
            return mode == "always" or bool(mcp_tool.get("write"))
        # Built-ins only ever touch Lumen's own workspace, so they are gated solely in
        # the strictest mode — asking to approve every `run_python` would make the agent
        # unusable at exactly the moment it is being most useful.
        return mode == "always" and bool(spec and spec.write)

    def _fail(self, ctx: RunContext, block: dict, message: str, status: str = "error") -> dict:
        block.update({"status": status, "ok": False, "summary": message[:200], "text": message})
        ctx.emit({"type": "tool.end", "index": block["index"], "id": block["id"], "ok": False,
                  "status": status, "summary": block["summary"], "ms": 0})
        ctx.guard.record(block["name"], block["args"], False)
        return {"ok": False, "error": message, "model_text": f"ERROR: {message}"}

    async def _invoke(self, ctx: RunContext, call: ToolCall, block: dict,
                      tools: dict[str, builtin.ToolSpec]) -> dict:
        started = time.time()
        spec = tools.get(call.name)
        key = signature(call.name, call.arguments)
        cached = ctx.tool_cache.get(key)
        if cached is not None:
            block.update({"status": "done", "ok": True, "cached": True,
                          "summary": f"{cached['summary']} (already fetched in this run)",
                          "text": cached["text"], "ms": 0})
            ctx.emit({"type": "tool.end", "index": block["index"], "id": block["id"], "ok": True,
                      "status": "done", "cached": True, "summary": block["summary"], "ms": 0,
                      "preview": cached["text"][:1200]})
            ctx.guard.record(call.name, call.arguments, True)
            return {"ok": True, "error": "",
                    "model_text": cached["text"] + "\n\n[identical call — this is the result "
                                                   "you already received earlier in this run]"}
        try:
            if spec is not None:
                result = await asyncio.wait_for(spec.handler(**(call.arguments or {})),
                                                timeout=self.c.settings.tool_timeout_s)
            else:
                result = await asyncio.wait_for(self.c.mcp.call(call.name, call.arguments),
                                                timeout=self.c.settings.tool_timeout_s)
        except asyncio.TimeoutError:
            result = {"ok": False,
                      "error": f"'{call.name}' did not return within {self.c.settings.tool_timeout_s}s."}
        except TypeError as exc:
            result = {"ok": False,
                      "error": f"Wrong arguments for '{call.name}': {exc}. Check the schema."}
        except Exception as exc:  # noqa: BLE001 - a broken tool must not end the run
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        elapsed = int((time.time() - started) * 1000)
        ok = bool(result.get("ok"))
        text = result.get("text") or result.get("error") or ""
        summary = result.get("summary") or (result.get("error") or "")[:200]
        block.update({"status": "done" if ok else "error", "ok": ok, "summary": summary,
                      "text": text[:40000], "ms": elapsed, "data": result.get("data")})
        ctx.usage["tool_calls"] += 1
        ctx.guard.record(call.name, call.arguments, ok)
        ctx.emit({"type": "tool.end", "index": block["index"], "id": block["id"], "ok": ok,
                  "status": block["status"], "summary": summary, "ms": elapsed,
                  "preview": text[:1200]})
        if ok:
            ctx.tool_cache[key] = {"summary": summary, "text": text[:40000]}
        model_text = text if ok else f"ERROR: {result.get('error', 'failed')}"
        return {"ok": ok, "error": result.get("error", ""), "model_text": model_text}

    # ------------------------------------------------------------------ critic
    async def _critique(self, ctx: RunContext, call: ToolCall, outcome: dict) -> dict:
        """A second opinion on a failure, from a call that sees only the failure.

        The model that just made the call is the worst judge of whether it worked — its
        own continuation pulls toward declaring victory. This one has no such pull.
        """
        ctx.emit({"type": "status", "phase": "checking"})
        payload = json.dumps({"tool": call.name, "arguments": call.arguments,
                              "error": outcome.get("error", "")[:1200]}, ensure_ascii=False)
        try:
            result = await self.c.fast_llm.chat(
                [{"role": "user", "content": payload}],
                system=prompts.CRITIC_SYSTEM, temperature=0.0, think=False,
                json_schema={"type": "object",
                             "properties": {"status": {"type": "string",
                                                       "enum": ["ok", "retry", "give_up"]},
                                            "reason": {"type": "string"},
                                            "advice": {"type": "string"}},
                             "required": ["status", "reason"]})
            verdict = json.loads(result.content)
        except Exception:
            # The critic is an optimisation, never load-bearing: if it cannot answer, the
            # loop keeps its own default (retry once with the raw error).
            return {"status": "retry", "reason": "", "advice": ""}
        status = verdict.get("status") if verdict.get("status") in ("ok", "retry", "give_up") else "retry"
        if ctx.guard.is_stagnant(call.name, call.arguments):
            status = "give_up"  # the guard remembers the pattern; the critic sees one result
        out = {"status": status, "reason": str(verdict.get("reason", ""))[:300],
               "advice": str(verdict.get("advice", ""))[:400]}
        ctx.emit({"type": "critic", "tool": call.name, **out})
        return out

    async def _reflect(self, ctx: RunContext, goal: str,
                       tools: dict[str, builtin.ToolSpec]) -> dict:
        """One gap-check before answering, and — if a gap is found — the call that closes it.

        The Critic names a concrete tool call and *this code* runs it. Handing the model a
        suggestion instead is how a run ends on "I would need to open that page": the
        instruction is understood, agreed with, and not acted on. Capped at one per run,
        read-only tools only, so it cannot become a loop or take an action nobody approved.
        """
        ctx.emit({"type": "status", "phase": "checking"})
        evidence = "\n".join(
            f"- {b['name']}({json.dumps(b.get('args') or {}, ensure_ascii=False)[:160]}): "
            f"{'ok' if b.get('ok') else 'FAILED'} — {b.get('summary', '')[:220]}"
            for b in ctx.blocks if b["type"] == "tool")
        draft = "\n".join(b.get("text", "") for b in ctx.blocks
                          if b["type"] == "text" and not b.get("superseded"))[:2000]
        readable = [name for name, spec in tools.items() if not spec.write]
        readable += [t["qualified_name"] for t in self.c.mcp.tools() if not t["write"]]
        try:
            result = await self.c.fast_llm.chat(
                [{"role": "user",
                  "content": f"Question: {goal}\n\nTools you may name: {', '.join(readable)}\n\n"
                             f"Evidence so far:\n{evidence}\n\nDraft answer:\n{draft}"}],
                system=prompts.REFLECT_SYSTEM, temperature=0.0, think=False,
                json_schema={"type": "object",
                             "properties": {"complete": {"type": "boolean"},
                                            "missing": {"type": "string"},
                                            "tool": {"type": "string"},
                                            "arguments": {"type": "object"}},
                             "required": ["complete"]})
            verdict = json.loads(result.content)
        except Exception:
            return {"complete": True}
        if verdict.get("complete", True):
            return {"complete": True}
        tool_name = str(verdict.get("tool") or "").strip()
        arguments = verdict.get("arguments") if isinstance(verdict.get("arguments"), dict) else {}
        missing = str(verdict.get("missing", ""))[:300]
        ctx.emit({"type": "critic", "tool": "reflection", "status": "gap", "reason": missing,
                  "advice": f"{tool_name}({json.dumps(arguments, ensure_ascii=False)[:160]})"
                            if tool_name else ""})
        if tool_name not in readable:
            return {"complete": False, "missing": missing}
        if ctx.guard.repeat_count(tool_name, arguments) >= 1:
            return {"complete": False, "missing": missing}  # already tried; nothing to gain
        return {"complete": False, "missing": missing, "tool": tool_name, "arguments": arguments}

    async def _run_gap_step(self, ctx: RunContext, tool_name: str, arguments: dict,
                            tools: dict[str, builtin.ToolSpec]) -> None:
        call = ToolCall(id=new_id("t"), name=tool_name, arguments=arguments)
        spec = tools.get(tool_name)
        mcp_tool = None if spec else self.c.mcp.resolve(tool_name)
        block = {"type": "tool", "index": len(ctx.blocks), "id": call.id, "name": tool_name,
                 "args": arguments, "by": "critic",
                 "server": mcp_tool["server_name"] if mcp_tool else (spec.group if spec else ""),
                 "kind": "mcp" if mcp_tool else "builtin", "status": "running",
                 "ok": None, "summary": "", "text": "", "ms": 0}
        ctx.blocks.append(block)
        ctx.emit({"type": "tool.start", "index": block["index"], "id": block["id"],
                  "name": tool_name, "args": arguments, "server": block["server"],
                  "kind": block["kind"], "by": "critic"})
        await self._invoke(ctx, call, block, tools)

    # ------------------------------------------------------------------ closing
    async def _final_answer(self, ctx: RunContext, question: str) -> None:
        """Compose the answer from the evidence, not from the conversation.

        Replaying the whole interleaved transcript is what makes a small model answer the
        last page it read instead of the question it was asked — by the end of a research
        run that transcript is fifteen thousand tokens of tool output in whatever language
        the sources happened to be in. This pass sees one thing: the question, and what the
        tools actually returned, labelled. It is also the only place the answer can come
        from on a failed run, which is why it states what could not be established rather
        than quietly omitting it.
        """
        ctx.emit({"type": "status", "phase": "writing"})
        evidence = self._evidence_digest(ctx)
        block = {"type": "text", "text": "", "index": len(ctx.blocks)}
        ctx.blocks.append(block)
        ctx.emit({"type": "block.open", "kind": "text", "index": block["index"]})

        def on_text(piece: str) -> None:
            block["text"] += piece
            ctx.emit({"type": "text.delta", "index": block["index"], "text": piece})

        try:
            result = await self.c.llm.chat(
                [{"role": "user", "content": prompts.synthesis_prompt(question, evidence)}],
                system=prompts.SYNTHESIS_SYSTEM, temperature=0.3, think=False,
                on_text=on_text)
            ctx.usage["llm_calls"] += 1
            ctx.usage["tokens_in"] += result.tokens_in
            ctx.usage["tokens_out"] += result.tokens_out
            ctx.emit({"type": "usage", **ctx.usage})
        except Exception as exc:  # noqa: BLE001
            failed = [b for b in ctx.blocks if b["type"] == "tool" and not b.get("ok")]
            block["text"] = (
                f"I could not produce an answer: {type(exc).__name__}: {exc}"
                + (f"\n\n{len(failed)} tool call(s) had already failed before this." if failed else ""))
            ctx.emit({"type": "text.delta", "index": block["index"], "text": block["text"]})

    def _evidence_digest(self, ctx: RunContext, budget: int = 14000) -> str:
        """What the tools returned, labelled, newest first, within a character budget.

        Newest first because the later calls are the ones that were made *knowing* what
        the earlier ones returned — when the budget runs out, the early exploratory calls
        are the right thing to lose. Failures are kept too, at one line each: "this could
        not be established" is part of an honest answer.
        """
        # The plan is excluded on purpose. It is a list of intentions, and a model shown
        # its own plan as "evidence" will report every step in it as accomplished — which
        # is precisely how a run that wrote no file ends up claiming it wrote and verified
        # one.
        tools = [b for b in ctx.blocks if b["type"] == "tool" and b.get("name") != "plan"]
        if not tools:
            return "(no tool was called — answer from your own knowledge, and say so.)"
        chunks: list[str] = []
        spent = 0
        for block in reversed(tools):
            args = json.dumps(block.get("args") or {}, ensure_ascii=False, default=str)[:300]
            head = f"## {block['name']}({args})"
            if not block.get("ok"):
                chunks.append(f"{head}\nFAILED: {block.get('summary', '')[:300]}")
                continue
            room = max(600, budget - spent)
            body = builtin.truncate_for_model(block.get("text") or "", min(room, 6000))
            spent += len(body)
            chunks.append(f"{head}\n{body}")
            if spent >= budget:
                chunks.append("[earlier calls omitted for length]")
                break
        return "\n\n".join(reversed(chunks))

    async def _maybe_title(self, conversation_id: str) -> None:
        conv = self.c.store.conversation(conversation_id)
        if conv is None or conv.get("title"):
            return
        first_user = next((m for m in conv["messages"] if m["role"] == "user"), None)
        if first_user is None:
            return
        try:
            result = await self.c.fast_llm.chat(
                [{"role": "user", "content": (first_user.get("content") or "")[:600]}],
                system=prompts.TITLE_SYSTEM, temperature=0.2, think=False, max_tokens=32)
            title = " ".join((result.content or "").strip().strip('"').split())[:60]
        except Exception:
            title = ""
        conv["title"] = title or (first_user.get("content") or "New conversation")[:60]
        self.c.store.touch()
        self.c.bus.emit("system", {"type": "conversation.title",
                                   "conversation_id": conversation_id, "title": conv["title"]})


def make_emitter(ctx: RunContext) -> Callable[[dict], None]:
    return ctx.emit
