"""The dependency container: every subsystem, constructed once, in dependency order.

No DI framework — for an app this size, a file you can read top to bottom beats magic
autowiring. Two pieces of behaviour beyond construction:

* `settings` is a *live view*, not a snapshot. Code reads `c.settings.max_iterations` and
  gets whatever Admin last set, falling back to the environment. Nothing has to know that
  a value is overridable.
* the model provider is rebuilt whenever that effective configuration changes, so picking
  a model in Admin takes effect on the next message with no restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.memory import Memory
from app.agent.skills import Skills
from app.audit import AuditLog
from app.config import Settings, settings as env_settings
from app.events import EventBus
from app.llm.provider import LLMProvider, make_provider
from app.mcp.registry import McpRegistry
from app.store import JsonStore

# What the UI may override at runtime. Anything outside this set is environment-only:
# paths, ports and the admin password are deployment facts, not preferences.
OVERRIDABLE = {
    "ollama_base_url", "model", "fast_model", "approval_mode", "max_iterations",
    "run_timeout_s", "tool_timeout_s", "enable_python_tool", "enable_web_tools",
    "python_timeout_s", "python_memory_mb", "num_ctx", "critic_min_tools",
    "history_turns", "parallel_max_fanout", "stagnation_limit", "max_retries",
    "tool_budget", "subagent_iterations", "subagent_timeout_s",
    "web_timeout_s", "mcp_call_timeout_s", "mcp_startup_timeout_s",
    "approval_timeout_s",
}

# Values Admin may set that have no environment counterpart.
EXTRA_DEFAULTS: dict[str, Any] = {"approval_mode": "writes"}


class _SettingsProxy:
    """`container.settings` — the effective value of every setting, read live."""

    def __init__(self, container: "Container") -> None:
        object.__setattr__(self, "_c", container)

    def __getattr__(self, name: str) -> Any:
        return object.__getattribute__(self, "_c").get(name)


class Container:
    def __init__(self) -> None:
        self.env: Settings = env_settings
        self.settings = _SettingsProxy(self)
        self.store = JsonStore(env_settings.db_path)
        self.bus = EventBus()
        self.memory = Memory(self.store, env_settings.memory_path)
        self.audit = AuditLog(env_settings.audit_path)
        # Skills share the memory database: both are things the agent keeps, and
        # one file is one thing to back up.
        self.skills = Skills(self.memory._db, self.store)
        self.mcp = McpRegistry(self.store, self.bus, self.settings)
        # What each source holds and how to read it — written once in Admin, read by
        # every run. See app/data/knowledge.py.
        from app.data.knowledge import Knowledge
        self.knowledge = Knowledge(self.store)
        self._llm: LLMProvider | None = None
        self._fast_llm: LLMProvider | None = None
        self._llm_key: tuple = ()
        from app.agent.runner import AgentRunner  # imported late: it types against this container

        self.runner = AgentRunner(self)

    # --- effective configuration ---------------------------------------------
    def prefs(self) -> dict[str, Any]:
        return self.store.prefs()

    def get(self, key: str) -> Any:
        prefs = self.store.prefs()
        if key in OVERRIDABLE and prefs.get(key) not in (None, ""):
            return prefs[key]
        if hasattr(self.env, key):
            return getattr(self.env, key)
        if key in prefs:
            return prefs[key]
        return EXTRA_DEFAULTS.get(key)

    def secret_values(self) -> list[str]:
        """Every credential this app holds, so a tool reply carrying one can be caught.

        These values are given to server *processes*, never to the model. If one comes
        back through a reply, something is echoing an environment it should not see — or
        the reply is crafted to put it in front of the model, which is the first step of
        every credential-theft chain an agent can be talked into.
        """
        found: list[str] = []
        for server in self.store.mcp_servers().values():
            for section in ("env", "headers"):
                for value in (server.get(section) or {}).values():
                    text = str(value).strip()
                    if len(text) >= 8:
                        found.append(text)
                        # A bearer header is a wrapper around the secret, not the secret.
                        if text.lower().startswith("bearer "):
                            found.append(text[7:].strip())
        password = (self.env.admin_password or "").strip()
        if len(password) >= 8:
            found.append(password)
        return found

    def workspace(self) -> Path:
        path = Path(self.env.workspace_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    # --- the model ------------------------------------------------------------
    def _refresh_llm(self) -> None:
        key = (self.get("ollama_base_url"), self.get("model"), self.get("fast_model"),
               self.get("num_ctx"))
        if key == self._llm_key and self._llm is not None:
            return
        base_url, model, fast_model, num_ctx = key
        timeout = int(self.env.llm_timeout_s)
        self._llm = make_provider(base_url, model or "", timeout, int(num_ctx or 0))
        self._fast_llm = make_provider(base_url, fast_model or model or "", timeout,
                                       int(num_ctx or 0))
        self._llm_key = key

    @property
    def llm(self) -> LLMProvider:
        self._refresh_llm()
        assert self._llm is not None
        return self._llm

    @property
    def fast_llm(self) -> LLMProvider:
        """A smaller model for the work that is not the answer — critic, reflection,
        titles. Falls back to the main model, so nothing depends on a second one existing."""
        self._refresh_llm()
        assert self._fast_llm is not None
        return self._fast_llm

    def invalidate_llm(self) -> None:
        self._llm_key = ()


container = Container()
