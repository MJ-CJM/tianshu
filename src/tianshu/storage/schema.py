"""Storage 建表 DDL —— 从 facade._create_tables 抽出，SQL 内容与拆分前完全一致。"""

SCHEMA_SQL_CORE = """
                CREATE TABLE IF NOT EXISTS edicts (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    context TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memorials (
                    id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    summary TEXT,
                    result TEXT,
                    final_output TEXT,
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    runtime_override_json TEXT,
                    acceptance_override_json TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
                    memorial_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decrees (
                    id TEXT PRIMARY KEY,
                    memorial_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    comment TEXT,
                    amended_goal TEXT,
                    actor TEXT NOT NULL DEFAULT 'human',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memorials_edict_id
                    ON memorials(edict_id);
                CREATE INDEX IF NOT EXISTS idx_events_edict_id
                    ON events(edict_id);
                CREATE INDEX IF NOT EXISTS idx_decrees_memorial_id
                    ON decrees(memorial_id);

                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    persona_id TEXT NOT NULL,
                    edict_id TEXT,
                    memorial_id TEXT,
                    category TEXT NOT NULL DEFAULT 'observation',
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'agent',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    entity_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    access_level TEXT NOT NULL DEFAULT 'private'
                );
                CREATE INDEX IF NOT EXISTS idx_memory_persona
                    ON memory_entries(persona_id);
                CREATE INDEX IF NOT EXISTS idx_memory_category
                    ON memory_entries(category);

                CREATE TABLE IF NOT EXISTS cost_ledger (
                    id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL,
                    memorial_id TEXT,
                    provider_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_cny REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cost_edict
                    ON cost_ledger(edict_id);
                CREATE INDEX IF NOT EXISTS idx_cost_created
                    ON cost_ledger(created_at);

                CREATE TABLE IF NOT EXISTS cost_budgets (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    budget_cny REAL NOT NULL,
                    spent_cny REAL NOT NULL DEFAULT 0.0,
                    period TEXT NOT NULL DEFAULT 'monthly',
                    reset_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS providers (
                    name TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    api_base TEXT,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    rpm_limit INTEGER,
                    tpm_limit INTEGER,
                    rpm_current INTEGER NOT NULL DEFAULT 0,
                    tpm_current INTEGER NOT NULL DEFAULT 0,
                    rpm_window_start TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    priority INTEGER NOT NULL DEFAULT 100,
                    cost_per_1k_prompt REAL,
                    cost_per_1k_completion REAL,
                    cost_per_1k_cache_read REAL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS plugins (
                    name TEXT PRIMARY KEY,
                    version TEXT NOT NULL DEFAULT '0.0.0',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    sha256 TEXT,
                    installed_at TEXT NOT NULL,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS llm_configs (
                    name TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    api_base TEXT NOT NULL DEFAULT '',
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    temperature REAL NOT NULL DEFAULT 0.7,
                    top_p REAL NOT NULL DEFAULT 1.0,
                    max_tokens INTEGER NOT NULL DEFAULT 4096,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dag_executions (
                    id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    root_memorial_id TEXT,
                    max_concurrency INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_dag_edict
                    ON dag_executions(edict_id);

                CREATE TABLE IF NOT EXISTS scheduler_jobs (
                    job_id TEXT PRIMARY KEY,
                    edict_id TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    cron_expr TEXT,
                    next_run TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    interval_seconds INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_edict
                    ON scheduler_jobs(edict_id);
                CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_status
                    ON scheduler_jobs(status);

                CREATE TABLE IF NOT EXISTS departments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS personas (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    title TEXT,
                    tools_allowed TEXT DEFAULT '[]',
                    tools_denied TEXT DEFAULT '[]',
                    tool_tier_max INTEGER DEFAULT 0,
                    can_delegate INTEGER DEFAULT 0,
                    delegates_to TEXT DEFAULT '[]',
                    soul_path TEXT,
                    role_path TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS dag_nodes (
                    node_id TEXT NOT NULL,
                    dag_execution_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending',
                    assigned_official TEXT,
                    assigned_worker TEXT,
                    tools_required_json TEXT NOT NULL DEFAULT '[]',
                    memorial_id TEXT,
                    checkpoint_json TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT,
                    PRIMARY KEY (dag_execution_id, node_id)
                );

                CREATE TABLE IF NOT EXISTS skill_metrics (
                    skill_name    TEXT PRIMARY KEY,
                    usage_count   INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at  TEXT,
                    created_at    TEXT,
                    created_by    TEXT NOT NULL DEFAULT 'manual',
                    source_edict_id TEXT,
                    state         TEXT NOT NULL DEFAULT 'active',
                    pinned        INTEGER NOT NULL DEFAULT 0,
                    archived_at   TEXT,
                    absorbed_into TEXT
                );

                CREATE TABLE IF NOT EXISTS universes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_universe_id TEXT,
                    status TEXT NOT NULL DEFAULT 'challenger',
                    origin TEXT NOT NULL DEFAULT 'manual_branch',
                    mutation_reason TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    fitness_json TEXT NOT NULL DEFAULT '{}',
                    code_ref TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_universe_single_champion
                    ON universes(status) WHERE status = 'champion';

                CREATE TABLE IF NOT EXISTS variant_eval_runs (
                    id TEXT PRIMARY KEY,
                    universe_id TEXT NOT NULL,
                    gate_passed INTEGER NOT NULL DEFAULT 0,
                    gate_detail TEXT,
                    fitness_json TEXT NOT NULL DEFAULT '{}',
                    eval_set_version TEXT,
                    cost REAL NOT NULL DEFAULT 0,
                    baseline_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_variant_eval_runs_universe
                    ON variant_eval_runs(universe_id);

                CREATE TABLE IF NOT EXISTS persona_metrics (
                    persona_id TEXT PRIMARY KEY,
                    total_executions INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    success_rate REAL NOT NULL DEFAULT 0.0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    avg_tokens_per_execution REAL NOT NULL DEFAULT 0.0,
                    total_cost_cny REAL NOT NULL DEFAULT 0.0,
                    avg_duration_seconds REAL NOT NULL DEFAULT 0.0,
                    synthesis_in_progress INTEGER NOT NULL DEFAULT 0,
                    synthesis_started_at TEXT,
                    tasks_since_last_synthesis INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS session_rules (
                    rule_id              TEXT PRIMARY KEY,
                    tool_name            TEXT NOT NULL,
                    arg_fingerprint      TEXT NOT NULL,
                    scope                TEXT NOT NULL CHECK (scope IN ('edict', 'always')),
                    edict_id             TEXT,
                    granted_at           TEXT NOT NULL,
                    granted_by_decree_id TEXT,
                    source               TEXT NOT NULL CHECK (source IN ('approval', 'profile', 'manual')),
                    reason               TEXT,
                    expires_at           TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_session_rules_tool_scope
                    ON session_rules(tool_name, scope, arg_fingerprint);

                CREATE INDEX IF NOT EXISTS idx_session_rules_edict
                    ON session_rules(edict_id);

                CREATE TABLE IF NOT EXISTS network_credentials (
                    id              TEXT PRIMARY KEY,
                    name            TEXT NOT NULL UNIQUE,
                    host_pattern    TEXT NOT NULL,
                    header_template TEXT NOT NULL,
                    extra_headers   TEXT NOT NULL DEFAULT '{}',
                    encrypted_value BLOB NOT NULL,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    last_used_at    TEXT,
                    deleted_at      TEXT,
                    kind            TEXT NOT NULL DEFAULT 'edict_auth',
                    provider_name   TEXT,
                    enabled         INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_netcreds_host ON network_credentials(host_pattern);
                CREATE INDEX IF NOT EXISTS idx_netcreds_name ON network_credentials(name);
                -- 注意：idx_netcreds_provider 需要 provider_name 列；老库迁移在 _migrate() 后建

                CREATE TABLE IF NOT EXISTS tool_switches (
                    tool_name  TEXT PRIMARY KEY,
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS engine_preferences (
                    id              TEXT PRIMARY KEY DEFAULT 'default',
                    fetch_chain     TEXT NOT NULL DEFAULT '[]',   -- JSON array, 空数组 = 不覆盖
                    search_provider TEXT,                          -- nullable, 空 = 不覆盖
                    fallback_mode   TEXT,                          -- nullable ("none" / "on_error_or_empty"), 空 = 不覆盖
                    updated_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outer_loop_iterations (
                    id              TEXT PRIMARY KEY,
                    edict_id        TEXT NOT NULL,
                    iteration       INTEGER NOT NULL,
                    level           TEXT NOT NULL,
                    actor_output    TEXT,
                    checks_result   TEXT,
                    critic_result   TEXT,
                    cost_cny        REAL DEFAULT 0,
                    started_at      TEXT NOT NULL,
                    finished_at     TEXT NOT NULL,
                    archived_at     TEXT,
                    UNIQUE (edict_id, iteration)
                );

                CREATE INDEX IF NOT EXISTS idx_outer_loop_archive
                    ON outer_loop_iterations(finished_at) WHERE archived_at IS NULL;

                CREATE TABLE IF NOT EXISTS outer_loop_checkpoints (
                    edict_id    TEXT PRIMARY KEY,
                    data_json   TEXT NOT NULL,
                    saved_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS supervision_reports (
                    edict_id          TEXT NOT NULL,
                    memorial_id       TEXT NOT NULL,
                    persona_id        TEXT NOT NULL,
                    persona_name      TEXT NOT NULL,
                    final_status      TEXT NOT NULL,
                    iterations_count  INTEGER NOT NULL,
                    total_cost_cny    REAL NOT NULL,
                    report_json       TEXT NOT NULL,
                    created_at        TEXT NOT NULL,
                    PRIMARY KEY (memorial_id, persona_id)
                );
                CREATE INDEX IF NOT EXISTS idx_supervision_edict
                    ON supervision_reports(edict_id);
            """

SCHEMA_SQL_FEISHU = """
                CREATE TABLE IF NOT EXISTS feishu_session_anchor (
                    instance_id      TEXT NOT NULL,
                    chat_id          TEXT NOT NULL,
                    current_edict_id TEXT,
                    updated_at       TIMESTAMP NOT NULL,
                    PRIMARY KEY (instance_id, chat_id)
                );
                CREATE TABLE IF NOT EXISTS feishu_seen_messages (
                    message_id  TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL DEFAULT 'feishu-default',
                    seen_at     TIMESTAMP NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feishu_seen_at ON feishu_seen_messages(seen_at);
                CREATE TABLE IF NOT EXISTS feishu_pending_cards (
                    approval_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL DEFAULT 'feishu-default',
                    chat_id     TEXT NOT NULL,
                    message_id  TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    created_at  TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feishu_thinking_messages (
                    memorial_id        TEXT PRIMARY KEY,
                    chat_id            TEXT NOT NULL,
                    message_id         TEXT NOT NULL,
                    source_message_id  TEXT NOT NULL DEFAULT '',
                    created_at         TIMESTAMP NOT NULL
                );
            """

SCHEMA_SQL_TELEGRAM = """
                CREATE TABLE IF NOT EXISTS telegram_session_anchor (
                    instance_id      TEXT NOT NULL,
                    chat_id          TEXT NOT NULL,
                    current_edict_id TEXT,
                    updated_at       TIMESTAMP NOT NULL,
                    PRIMARY KEY (instance_id, chat_id)
                );
                CREATE TABLE IF NOT EXISTS telegram_seen_messages (
                    update_id   TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL DEFAULT 'telegram-default',
                    seen_at     TIMESTAMP NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tg_seen_at ON telegram_seen_messages(seen_at);
                CREATE TABLE IF NOT EXISTS telegram_pending_buttons (
                    approval_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL DEFAULT 'telegram-default',
                    chat_id     TEXT NOT NULL,
                    message_id  TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    created_at  TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_thinking_messages (
                    memorial_id TEXT PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    message_id  TEXT NOT NULL,
                    created_at  TIMESTAMP NOT NULL
                );
            """

SCHEMA_SQL_CHANNELS = """
                CREATE TABLE IF NOT EXISTS channel_configs (
                    channel_type     TEXT PRIMARY KEY,
                    config_json      TEXT NOT NULL,
                    encrypted_secret BLOB,
                    updated_at       TIMESTAMP NOT NULL
                );

                -- 多 bot 实例：每个实例独立的 channel 配置 + 凭证（instance_id 维度）。
                CREATE TABLE IF NOT EXISTS channel_instances (
                    instance_id      TEXT PRIMARY KEY,
                    channel_type     TEXT NOT NULL,
                    label            TEXT NOT NULL DEFAULT '',
                    enabled          INTEGER NOT NULL DEFAULT 1,
                    config_json      TEXT NOT NULL,
                    encrypted_secret BLOB,
                    updated_at       TIMESTAMP NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_channel_instances_type ON channel_instances(channel_type);

                -- 藏兵阁 · MCP server DB 配置（既能 override YAML 种子，也能完整定义新 server）。
                -- nullable 字段语义：
                --   * 若 YAML 中存在同名 server：NULL = 沿用 YAML；非 NULL = 覆写
                --   * 若 YAML 中无同名 server：DB 必须填够 transport + 主字段，merge 时晋级为完整 server
                CREATE TABLE IF NOT EXISTS mcp_server_overrides (
                    name                 TEXT PRIMARY KEY,
                    enabled              INTEGER,
                    env_json             TEXT,
                    tools_include_json   TEXT,
                    tools_exclude_json   TEXT,
                    transport            TEXT,           -- "stdio" | "streamable_http"
                    command              TEXT,
                    args_json            TEXT,
                    url                  TEXT,
                    headers_json         TEXT,
                    default_tier         INTEGER,
                    timeout              INTEGER,
                    connect_timeout      INTEGER,
                    tool_overrides_json  TEXT,
                    updated_at           TEXT NOT NULL
                );
            """
