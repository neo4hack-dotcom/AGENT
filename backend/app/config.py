"""Every knob Agent has, in one place, with a single env prefix.

The rule that shapes this file: nothing here is required. Agent boots with an empty
`.env`, the whole UI loads, and the only thing that fails is the exact feature whose
dependency you have not connected yet — with a message that names the fix.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    app_name: str = "AGENT"
    port: int = 3041
    db_path: str = "data/agent.json"
    static_dir: str = ""  # empty => auto-detect ../frontend/dist

    # --- The model. Local only, by design: Ollama on this machine. ---
    ollama_base_url: str = "http://localhost:11434"
    # gpt-oss is the default because it is the strongest model this Ollama offers. Note
    # the `-cloud` tag: it is hosted by Ollama, not by this machine, and the interface
    # says so on every screen rather than letting the "local" claim quietly go stale.
    model: str = "gpt-oss:120b-cloud"
    # The critic, the gap check and titles — never the answer. A small local model here
    # keeps those off the network and off the clock.
    fast_model: str = "qwen3.5:4b"
    llm_timeout_s: int = 900
    num_ctx: int = 0      # 0 => let Ollama use the model's own default

    # --- Admin ---
    # Empty password keeps the admin area reachable from this machine only. Set one and
    # it is required from everywhere, loopback included.
    admin_password: str = ""
    session_ttl_s: int = 604800

    # --- Agent guardrails: sane defaults, all overridable per deployment ---
    max_iterations: int = 14      # tool-calling turns in one answer
    max_retries: int = 2          # attempts on one failing tool before escalating
    stagnation_limit: int = 2     # identical structural failures before the guard fires
    run_timeout_s: int = 1200     # wall clock for one answer
    tool_timeout_s: int = 180
    # How long a sensitive step waits for a human. The run's own clock is paused
    # meanwhile, so this is patience, not budget.
    approval_timeout_s: int = 300
    parallel_max_fanout: int = 4  # concurrent tool calls in one turn
    history_turns: int = 20       # conversation turns replayed into the prompt
    critic_min_tools: int = 1     # tool calls before the pre-answer reflection runs

    # --- Built-in tools ---
    workspace_dir: str = "data/workspace"   # the only directory file tools may touch
    enable_python_tool: bool = True
    python_timeout_s: int = 45
    python_memory_mb: int = 2048
    enable_web_tools: bool = True
    web_fetch_max_bytes: int = 3_000_000
    web_timeout_s: int = 30

    # --- MCP ---
    mcp_autoconnect: bool = True
    mcp_startup_timeout_s: int = 45
    mcp_call_timeout_s: int = 180
    mcp_protocol_version: str = "2025-06-18"


settings = Settings()
