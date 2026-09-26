"""Every knob Agent has, in one place, with a single env prefix.

The rule that shapes this file: nothing here is required. Agent boots with an empty
`.env`, the whole UI loads, and the only thing that fails is the exact feature whose
dependency you have not connected yet — with a message that names the fix.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    # The air gap. A property of the deployment, set in the environment (AGENT_AIRGAPPED),
    # never a preference the UI can flip: whoever can reach Admin should not be able to
    # open a path out of the network. See app/network.py.
    airgapped: bool = True
    # Domain suffixes that count as internal even before resolution, e.g. ".corp.example".
    internal_domains: str = ""
    # Testing escape hatch for the model only (AGENT_ALLOW_CLOUD_MODEL): every other part of
    # the air gap stays enforced. Never set it where the data is real.
    allow_cloud_model: bool = False

    app_name: str = "AGENT"
    port: int = 3041
    db_path: str = "data/agent.json"
    # Memory lives in its own SQLite file: full-text search wants an index, and
    # the transcript store wants to stay a readable JSON document.
    memory_path: str = "data/memory.db"
    # Append-only, hash-chained. See app/audit.py for why the chain is there.
    audit_path: str = "data/audit.jsonl"
    static_dir: str = ""  # empty => auto-detect ../frontend/dist

    # --- The model. Local only, by design: Ollama on this machine. ---
    ollama_base_url: str = "http://localhost:11434"
    # A model that runs on this Ollama. Cloud tags (`-cloud`) send prompts and data to
    # ollama.com and are refused while the deployment is air-gapped.
    model: str = "gpt-oss:20b"
    # The critic, the gap check, compaction and titles — never the answer. This one runs
    # several times per question, so on a machine that cannot hold two models at once it is
    # what makes the whole app feel slow. Point it at a local model to keep that work on
    # the machine; the header says which of the two you are running.
    fast_model: str = ""   # empty => the main model is reused
    llm_timeout_s: int = 900
    num_ctx: int = 0      # 0 => let Ollama use the model's own default

    # --- Access ---
    # Empty password keeps the admin area reachable from this machine only. Set one and
    # it is required from everywhere, loopback included.
    admin_password: str = ""
    # Lets people on other machines use the agent (chat, not admin) after signing in. With
    # neither password set, the app answers this machine only. See app/security.py.
    access_password: str = ""
    # Host names this app may be reached under besides localhost, comma-separated — e.g.
    # "agent.corp.example" or ".corp.example". Anything else is refused: that is what stops
    # DNS rebinding, where a hostile domain re-resolves to this machine.
    allowed_hosts: str = ""
    session_ttl_s: int = 604800

    # --- Agent guardrails: sane defaults, all overridable per deployment ---
    # Tool-calling turns in one answer. 14 was a budget, not a limit, and it cut off the
    # class of work this app exists for: a cross-source question — query a database, read
    # two files, recompute a second way, write the report — spends four turns just finding
    # its inputs and needs about 28. The wall clock below is the real safety net, and the
    # loop guard stops a repeating agent long before this number does.
    max_iterations: int = 30
    max_retries: int = 2          # attempts on one failing tool before escalating
    stagnation_limit: int = 2     # identical structural failures before the guard fires
    run_timeout_s: int = 1200     # wall clock for one answer
    tool_timeout_s: int = 180
    # How long a sensitive step waits for a human. The run's own clock is paused
    # meanwhile, so this is patience, not budget.
    approval_timeout_s: int = 300
    parallel_max_fanout: int = 4  # concurrent tool calls in one turn
    history_turns: int = 20       # conversation turns replayed into the prompt
    # How many MCP tools to put in front of the model per turn. The rest stay
    # reachable through find_tools; see agent/context.py.
    tool_budget: int = 28
    # The quarantined reader's own ceilings, separate from the parent run's.
    critic_min_tools: int = 1     # tool calls before the pre-answer reflection runs

    # Numbers and dates in charts and reports: "fr-FR" (1 234,56 · janv. 2026), "en-US",
    # "en-GB". Labels themselves follow the language of the question.
    chart_locale: str = "fr-FR"

    # --- Built-in tools ---
    workspace_dir: str = "data/workspace"   # the only directory file tools may touch
    enable_python_tool: bool = True
    python_timeout_s: int = 45
    python_memory_mb: int = 2048

    # --- MCP ---
    mcp_autoconnect: bool = True
    mcp_startup_timeout_s: int = 45
    mcp_call_timeout_s: int = 180
    mcp_protocol_version: str = "2025-06-18"


settings = Settings()
