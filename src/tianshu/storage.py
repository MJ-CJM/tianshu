"""SQLite storage layer - system truth source."""

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ulid import ULID

from tianshu.models import (
    AuditResult,
    DAGExecution,
    DAGNode,
    DAGNodeStatus,
    Decree,
    Edict,
    EdictDispatch,
    EdictRuntime,
    EdictSchedule,
    EdictStatus,
    Memorial,
    TaskStatus,
    UsageSummary,
)
from tianshu.models.acceptance import AcceptanceCriteria

logger = logging.getLogger(__name__)


def _audit_passed(a: dict) -> bool:
    """AuditResult.verdict == 'pass' を「合格」とみなす（conservative: 不明は不合格）。"""
    return a.get("verdict") == "pass"


# Deferred import to avoid circular deps
_MemoryEntry = None


def _get_memory_entry():
    global _MemoryEntry
    if _MemoryEntry is None:
        from tianshu.memory.models import MemoryEntry
        _MemoryEntry = MemoryEntry
    return _MemoryEntry


class Storage:
    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).expanduser())
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def init_db(self) -> None:
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._migrate()
        self._init_fts()

    def _init_fts(self) -> None:
        """Initialize FTS5 full-text search for memory entries."""
        from tianshu.memory.fts import create_fts_table
        self._fts_available = create_fts_table(self._conn)

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.executescript("""
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
            """)
            self._conn.executescript("""
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
            """)
            # --- Telegram（与飞书并列；表结构对应飞书，message_id 语义不同）---
            self._conn.executescript("""
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
            """)
            self._conn.executescript("""
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
            """)

    def _migrate(self) -> None:
        migrations = [
            # Phase 0 migrations
            "ALTER TABLE edicts ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
            "ALTER TABLE memorials ADD COLUMN instruction TEXT",
            "ALTER TABLE edicts ADD COLUMN title TEXT NOT NULL DEFAULT ''",
            # Phase 1 edict migrations
            "ALTER TABLE edicts ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE edicts ADD COLUMN source TEXT NOT NULL DEFAULT 'api'",
            "ALTER TABLE edicts ADD COLUMN submitter TEXT",
            "ALTER TABLE edicts ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'",
            "ALTER TABLE edicts ADD COLUMN review_policy TEXT NOT NULL DEFAULT 'never'",
            "ALTER TABLE edicts ADD COLUMN output_format TEXT",
            "ALTER TABLE edicts ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE edicts ADD COLUMN schedule_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE edicts ADD COLUMN dispatch_json TEXT",
            "ALTER TABLE edicts ADD COLUMN runtime_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE edicts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
            # Phase 1 memorial migrations
            "ALTER TABLE memorials ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE memorials ADD COLUMN parent_memorial_id TEXT",
            "ALTER TABLE memorials ADD COLUMN review_status TEXT NOT NULL DEFAULT 'not_required'",
            "ALTER TABLE memorials ADD COLUMN audit_json TEXT",
            "ALTER TABLE memorials ADD COLUMN artifacts_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE memorials ADD COLUMN timeline_json TEXT NOT NULL DEFAULT '[]'",
            # Phase 3 memorial migrations
            "ALTER TABLE memorials ADD COLUMN dag_node_id TEXT",
            "ALTER TABLE memorials ADD COLUMN persona_id TEXT",
            # USD → CNY column renames
            "ALTER TABLE cost_ledger RENAME COLUMN cost_usd TO cost_cny",
            "ALTER TABLE cost_budgets RENAME COLUMN budget_usd TO budget_cny",
            "ALTER TABLE cost_budgets RENAME COLUMN spent_usd TO spent_cny",
            # Phase 2: persona skills_allowed
            "ALTER TABLE personas ADD COLUMN skills_allowed TEXT DEFAULT '[]'",
            # Phase 2: persona → LLM config binding
            "ALTER TABLE personas ADD COLUMN llm_config_name TEXT",
            # Phase 2.1: edict → assigned persona
            "ALTER TABLE edicts ADD COLUMN assigned_persona_id TEXT",
            # Planner persona: use a specific cabinet persona's LLM config for planning
            "ALTER TABLE edicts ADD COLUMN planner_persona_id TEXT",
            # Phase 2.2: plan review — require human approval before execution
            "ALTER TABLE edicts ADD COLUMN plan_review INTEGER DEFAULT 0",
            # 2026-04-22: network_credentials 加 kind/provider_name 区分 edict_auth vs engine_provider
            "ALTER TABLE network_credentials ADD COLUMN kind TEXT NOT NULL DEFAULT 'edict_auth'",
            "ALTER TABLE network_credentials ADD COLUMN provider_name TEXT",
            # 2026-04-22: provider_name 列就绪后建 partial index（必须放在 ALTER 之后）
            "CREATE INDEX IF NOT EXISTS idx_netcreds_provider "
            "ON network_credentials(provider_name) WHERE provider_name IS NOT NULL",
            # 2026-04-22: 为存量软删除记录让出 name（防止新建同名凭证时 IntegrityError）
            "UPDATE network_credentials "
            "SET name = name || '__deleted_' || id "
            "WHERE deleted_at IS NOT NULL AND name NOT LIKE '%__deleted_%'",
            # 2026-04-22: network_credentials 加 enabled 列（启停开关；disabled 视为未配置）
            "ALTER TABLE network_credentials ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
            # 2026-04-26: 长任务 outer loop 字段
            "ALTER TABLE edicts ADD COLUMN acceptance_json TEXT",
            "ALTER TABLE edicts ADD COLUMN execution_profile TEXT NOT NULL DEFAULT 'foreground'",
            # 2026-04-27: providers 加 cache 命中价（NULL = fallback 到 cost_per_1k_prompt）
            "ALTER TABLE providers ADD COLUMN cost_per_1k_cache_read REAL",
            # 2026-04-27: supervision_reports PK 从 (edict_id) 改为 (edict_id, persona_id) 支持多监督官
            "DROP TABLE IF EXISTS supervision_reports",
            """CREATE TABLE supervision_reports (
                edict_id          TEXT NOT NULL,
                persona_id        TEXT NOT NULL,
                persona_name      TEXT NOT NULL,
                final_status      TEXT NOT NULL,
                iterations_count  INTEGER NOT NULL,
                total_cost_cny    REAL NOT NULL,
                report_json       TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                PRIMARY KEY (edict_id, persona_id)
            )""",
            # 2026-04-28: supervision_reports 加 memorial_id 列；
            # PK 改为 (memorial_id, persona_id)，让 follow-up 多条奏折各有独立报告。
            "ALTER TABLE supervision_reports ADD COLUMN memorial_id TEXT",
            # 老行回填：取该 edict 最新 memorial 的 id
            """UPDATE supervision_reports
                  SET memorial_id = (
                      SELECT id FROM memorials
                       WHERE memorials.edict_id = supervision_reports.edict_id
                       ORDER BY created_at DESC LIMIT 1
                  )
                  WHERE memorial_id IS NULL""",
            # 重建表 (SQLite 不支持改 PK) — 拷贝 + drop + rename
            """CREATE TABLE IF NOT EXISTS _supervision_reports_new (
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
            )""",
            """INSERT OR IGNORE INTO _supervision_reports_new
                   (edict_id, memorial_id, persona_id, persona_name, final_status,
                    iterations_count, total_cost_cny, report_json, created_at)
                   SELECT edict_id, memorial_id, persona_id, persona_name, final_status,
                          iterations_count, total_cost_cny, report_json, created_at
                     FROM supervision_reports
                    WHERE memorial_id IS NOT NULL""",
            "DROP TABLE supervision_reports",
            "ALTER TABLE _supervision_reports_new RENAME TO supervision_reports",
            "CREATE INDEX IF NOT EXISTS idx_supervision_edict "
            "ON supervision_reports(edict_id)",
            # 2026-04-28: follow-up 时本次 memorial 单独覆盖 edict.runtime / acceptance
            "ALTER TABLE memorials ADD COLUMN runtime_override_json TEXT",
            "ALTER TABLE memorials ADD COLUMN acceptance_override_json TEXT",
            # 2026-04-30: DeepSeek reasoner / 新版 thinking-mode 模型 follow_up 时
            # 必须把上一轮 reasoning_content 一起回传，否则 400 invalid_request_error
            "ALTER TABLE memorials ADD COLUMN reasoning_content TEXT",
            # 2026-04-30: 飞书 typing reaction 替代 thinking 卡片
            "ALTER TABLE feishu_thinking_messages ADD COLUMN source_message_id TEXT NOT NULL DEFAULT ''",
            # 2026-04-30: persona 加 title（部门内职务，例：大学士、协理通政）
            "ALTER TABLE personas ADD COLUMN title TEXT",
            # 2026-05-07: MCP server DB 配置升级 — 让 DB 能完整定义新 server，不再仅 override YAML
            "ALTER TABLE mcp_server_overrides ADD COLUMN transport TEXT",
            "ALTER TABLE mcp_server_overrides ADD COLUMN command TEXT",
            "ALTER TABLE mcp_server_overrides ADD COLUMN args_json TEXT",
            "ALTER TABLE mcp_server_overrides ADD COLUMN url TEXT",
            "ALTER TABLE mcp_server_overrides ADD COLUMN headers_json TEXT",
            "ALTER TABLE mcp_server_overrides ADD COLUMN default_tier INTEGER",
            "ALTER TABLE mcp_server_overrides ADD COLUMN timeout INTEGER",
            "ALTER TABLE mcp_server_overrides ADD COLUMN connect_timeout INTEGER",
            "ALTER TABLE mcp_server_overrides ADD COLUMN tool_overrides_json TEXT",
            # 2026-05-09: 最终交付物字段 —— 与 result（含中间过程）分离，
            # 外发渠道（飞书/邮件等）优先用此字段，只呈现"用户关心的产物"。
            "ALTER TABLE memorials ADD COLUMN final_output TEXT",
            # 2026-05-19: engine_preferences 加浏览器引擎启停开关
            "ALTER TABLE engine_preferences ADD COLUMN scrapling_dynamic_enabled INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE engine_preferences ADD COLUMN scrapling_stealthy_enabled INTEGER NOT NULL DEFAULT 0",
            # 2026-05-19: 纠正存量 jina-only override（欠费 key 导致定时任务连续失败）
            """UPDATE engine_preferences
                  SET fetch_chain = '["scrapling", "local"]',
                      search_provider = 'duckduckgo',
                      fallback_mode = 'on_error_or_empty'
                WHERE id = 'default' AND fetch_chain = '["jina"]'""",
            # 2026-06-05: skill_metrics 生命周期/策展字段（修撰 SkillCurator）
            "ALTER TABLE skill_metrics ADD COLUMN state TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE skill_metrics ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE skill_metrics ADD COLUMN archived_at TEXT",
            "ALTER TABLE skill_metrics ADD COLUMN absorbed_into TEXT",
            # 2026-06-05: scheduler_jobs 周期间隔（interval 类型，配合调度工具 schedule_edict）
            "ALTER TABLE scheduler_jobs ADD COLUMN interval_seconds INTEGER",
            # Phase 8: persona 全局记忆读开关
            "ALTER TABLE personas ADD COLUMN memory_global_read INTEGER DEFAULT 0",
            # 2026-06-07: skill_metrics 人在回路字段（前景主导技能学习）
            "ALTER TABLE skill_metrics ADD COLUMN human_curated INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE skill_metrics ADD COLUMN last_human_action TEXT",
            # 2026-06-07: 平行位面 — memorial 归因到所在位面
            "ALTER TABLE memorials ADD COLUMN universe_id TEXT",
            # 2026-06-08: 平行位面 1b — 诏令结果显式反馈分（+1 赞 / -1 踩 / 0 无）
            "ALTER TABLE memorials ADD COLUMN feedback_score INTEGER NOT NULL DEFAULT 0",
            # 2026-06-08: 平行位面 1b — universe_id 索引（fitness 聚合按 universe_id 扫描）
            "CREATE INDEX IF NOT EXISTS idx_memorials_universe_id ON memorials(universe_id)",
            # 2026-06-08: 代码变体位面 2a — worktree 分支引用
            "ALTER TABLE universes ADD COLUMN code_ref TEXT",
        ]
        for sql in migrations:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e) and "no such column" not in str(e):
                    raise

        # persona_metrics columns for PROFILE synthesis locking (2026-04-18)
        for col, ddl in [
            ("synthesis_in_progress", "INTEGER NOT NULL DEFAULT 0"),
            ("synthesis_started_at", "TEXT"),
            ("tasks_since_last_synthesis", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                self._conn.execute(
                    f"ALTER TABLE persona_metrics ADD COLUMN {col} {ddl}"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e) and "no such column" not in str(e):
                    raise
        self._conn.commit()

        # 2026-06-04: 多 bot 实例 —— 会话/审批表加 instance_id 维度（幂等）
        self._migrate_session_tables_add_instance()

        # Seed departments from existing personas (one-time)
        self._seed_departments()

    def _migrate_session_tables_add_instance(self) -> None:
        """为存量 DB 的飞书/telegram 会话表补 instance_id 维度（幂等）。

        - anchor 表：PK 改为 (instance_id, chat_id)，存量行回填 '<channel>-default'。
        - pending/seen 表：ALTER ADD COLUMN instance_id（带 default）。
        - thinking 表不动（memorial_id 全局唯一）。
        """
        cols = {
            r[1]
            for r in self._conn.execute(
                "PRAGMA table_info(telegram_session_anchor)"
            ).fetchall()
        }
        if "instance_id" in cols:
            return  # 已迁移

        # anchor 表：SQLite 不支持改 PK，需重建 + 拷贝。
        for channel in ("feishu", "telegram"):
            default_iid = f"{channel}-default"
            table = f"{channel}_session_anchor"
            self._conn.executescript(
                f"""
                CREATE TABLE {table}_new (
                    instance_id      TEXT NOT NULL,
                    chat_id          TEXT NOT NULL,
                    current_edict_id TEXT,
                    updated_at       TIMESTAMP NOT NULL,
                    PRIMARY KEY (instance_id, chat_id)
                );
                INSERT INTO {table}_new (instance_id, chat_id, current_edict_id, updated_at)
                    SELECT '{default_iid}', chat_id, current_edict_id, updated_at FROM {table};
                DROP TABLE {table};
                ALTER TABLE {table}_new RENAME TO {table};
                """
            )

        # pending / seen 表：ALTER ADD COLUMN（带 default，存量行自动回填）。
        for sql in (
            "ALTER TABLE feishu_pending_cards ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'feishu-default'",
            "ALTER TABLE telegram_pending_buttons ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'telegram-default'",
            "ALTER TABLE feishu_seen_messages ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'feishu-default'",
            "ALTER TABLE telegram_seen_messages ADD COLUMN "
            "instance_id TEXT NOT NULL DEFAULT 'telegram-default'",
        ):
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
        self._conn.commit()

    def _seed_departments(self) -> None:
        """Populate departments table from existing personas if empty."""
        count = self._conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if count > 0:
            return

        KNOWN = {
            "bingbu": "兵部 (Ministry of War)",
            "neige": "内阁 (Imperial Cabinet)",
            "ducha": "都察院 (Censorate)",
            "tongzheng": "通政司 (Bureau of Coordination)",
            "wenyuan": "文渊阁 (Grand Secretariat)",
            "hubu": "户部 (Ministry of Revenue)",
        }

        now = datetime.now(UTC).isoformat()
        # Collect distinct departments from personas
        rows = self._conn.execute(
            "SELECT DISTINCT department FROM personas"
        ).fetchall()
        dept_ids = {r[0] for r in rows}
        # Merge with known defaults
        dept_ids.update(KNOWN.keys())

        with self._conn:
            for dept_id in dept_ids:
                name = KNOWN.get(dept_id, dept_id)
                self._conn.execute(
                    "INSERT OR IGNORE INTO departments (id, name, description, created_at) VALUES (?, ?, '', ?)",
                    (dept_id, name, now),
                )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Edict ---

    def save_edict(self, edict: Edict) -> None:
        acceptance_json = (
            edict.acceptance.model_dump_json() if edict.acceptance else None
        )
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO edicts
                   (id, title, goal, context, status, created_at,
                    idempotency_key, source, submitter, priority, review_policy,
                    output_format, constraints_json, schedule_json, dispatch_json,
                    runtime_json, metadata_json, assigned_persona_id,
                    planner_persona_id, plan_review, acceptance_json, execution_profile)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    edict.id,
                    edict.title,
                    edict.goal,
                    edict.context,
                    edict.status.value,
                    edict.created_at.isoformat(),
                    edict.idempotency_key,
                    edict.source,
                    edict.submitter,
                    edict.priority,
                    edict.review_policy,
                    edict.output_format,
                    json.dumps(edict.constraints),
                    edict.schedule.model_dump_json(),
                    edict.dispatch.model_dump_json() if edict.dispatch else None,
                    edict.runtime.model_dump_json(),
                    json.dumps(edict.metadata, default=str),
                    edict.assigned_persona_id,
                    edict.planner_persona_id,
                    int(edict.plan_review),
                    acceptance_json,
                    edict.execution_profile,
                ),
            )

    def get_edict(self, edict_id: str) -> Edict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM edicts WHERE id = ?", (edict_id,)
            ).fetchone()
        return self._row_to_edict(row) if row else None

    def list_edicts(
        self,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
        exclude_assistant_chat: bool = False,
        instance_id: str | None = None,
    ) -> tuple[list[Edict], int]:
        """列敕令。

        exclude_assistant_chat=True 时过滤掉 metadata.assistant_chat=true 的聊天敕令。
        SQL 用 json_extract(metadata_json, '$.assistant_chat') 实现（SQLite 中 JSON true → 整数 1）。

        instance_id=None 时不按实例过滤（web 全局视图）；指定时只返回
        metadata.instance_id 匹配的敕令。仅当实例 id 以 ``-default`` 结尾时，
        额外纳入无 instance_id 的存量敕令中 metadata.channel 等于该实例 channel
        前缀的（向后兼容旧敕令）；非 default 实例不继承未打标的旧敕令。
        """
        conditions: list[str] = []
        params: list[str | int] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if search:
            conditions.append("(title LIKE ? OR goal LIKE ?)")
            params.append(f"%{search}%")
            params.append(f"%{search}%")
        if instance_id is not None:
            if instance_id.endswith("-default"):
                channel_prefix = instance_id[: -len("-default")]
                conditions.append(
                    "(json_extract(metadata_json, '$.instance_id') = ? "
                    " OR (json_extract(metadata_json, '$.instance_id') IS NULL "
                    "     AND json_extract(metadata_json, '$.channel') = ?))"
                )
                params.extend([instance_id, channel_prefix])
            else:
                conditions.append("json_extract(metadata_json, '$.instance_id') = ?")
                params.append(instance_id)
        if exclude_assistant_chat:
            conditions.append(
                "(json_extract(metadata_json, '$.assistant_chat') IS NULL "
                "OR json_extract(metadata_json, '$.assistant_chat') != 1)"
            )
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM edicts{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM edicts{where}",
                params,
            ).fetchone()[0]
        return [self._row_to_edict(r) for r in rows], total

    def update_edict(
        self, edict_id: str, title: str | None = None, goal: str | None = None, context: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[str] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if goal is not None:
            sets.append("goal = ?")
            params.append(goal)
        if context is not None:
            sets.append("context = ?")
            params.append(context)
        if not sets:
            return
        params.append(edict_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE edicts SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def delete_edict(self, edict_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM events WHERE edict_id = ?", (edict_id,))
            self._conn.execute("DELETE FROM memorials WHERE edict_id = ?", (edict_id,))
            self._conn.execute("DELETE FROM edicts WHERE id = ?", (edict_id,))

    def update_edict_status(self, edict_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE edicts SET status = ? WHERE id = ?",
                (status, edict_id),
            )

    def update_edict_lifecycle_phase(self, edict_id: str, phase: str) -> None:
        """部分更新 runtime_json 的 lifecycle_phase 字段，保留其他字段。"""
        if phase not in ("active", "paused", "winding_down", "complete"):
            raise ValueError(f"unknown lifecycle_phase: {phase}")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT runtime_json FROM edicts WHERE id = ?", (edict_id,),
            ).fetchone()
            if not row:
                return
            runtime = json.loads(row["runtime_json"] or "{}")
            runtime["lifecycle_phase"] = phase
            self._conn.execute(
                "UPDATE edicts SET runtime_json = ? WHERE id = ?",
                (json.dumps(runtime), edict_id),
            )

    # --- Memorial ---

    def save_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO memorials
                   (id, edict_id, instruction, status, summary, result, usage_json,
                    error, created_at, started_at, completed_at,
                    attempt, parent_memorial_id, review_status, audit_json,
                    artifacts_json, timeline_json, dag_node_id, persona_id,
                    runtime_override_json, acceptance_override_json,
                    reasoning_content, final_output, universe_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._memorial_to_params(memorial),
            )

    def update_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE memorials SET
                   status=?, summary=?, result=?, usage_json=?, error=?,
                   started_at=?, completed_at=?,
                   attempt=?, review_status=?, audit_json=?,
                   artifacts_json=?, timeline_json=?,
                   dag_node_id=?, persona_id=?,
                   runtime_override_json=?, acceptance_override_json=?,
                   reasoning_content=?, final_output=?, universe_id=?
                   WHERE id=?""",
                (
                    memorial.status.value,
                    memorial.summary,
                    memorial.result,
                    memorial.usage.model_dump_json(),
                    memorial.error,
                    memorial.started_at.isoformat() if memorial.started_at else None,
                    memorial.completed_at.isoformat() if memorial.completed_at else None,
                    memorial.attempt,
                    memorial.review_status,
                    memorial.audit.model_dump_json() if memorial.audit else None,
                    json.dumps([a.model_dump() for a in memorial.artifacts], default=str),
                    json.dumps([t.model_dump() for t in memorial.timeline], default=str),
                    memorial.dag_node_id,
                    memorial.persona_id,
                    json.dumps(memorial.runtime_override) if memorial.runtime_override else None,
                    memorial.acceptance_override.model_dump_json() if memorial.acceptance_override else None,
                    memorial.reasoning_content,
                    memorial.final_output,
                    memorial.universe_id,
                    memorial.id,
                ),
            )

    def update_memorial_usage(self, memorial_id: str, usage: UsageSummary) -> None:
        """只更新 memorial.usage_json 字段。

        与 update_memorial 的差别：避免回写整个 memorial（外环 critic 回写 cost 时只关心 usage）。
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memorials SET usage_json = ? WHERE id = ?",
                (usage.model_dump_json(), memorial_id),
            )

    def get_memorial(self, memorial_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE id = ?", (memorial_id,)
            ).fetchone()
        return self._row_to_memorial(row) if row else None

    def get_memorial_by_edict(self, edict_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
                (edict_id,),
            ).fetchone()
        return self._row_to_memorial(row) if row else None

    def list_memorials_by_edict(self, edict_id: str) -> list[Memorial]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [self._row_to_memorial(r) for r in rows]

    def list_memorials(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Memorial], int]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM memorials WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM memorials WHERE status = ?", (status,)
                ).fetchone()[0]
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memorials ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = self._conn.execute("SELECT COUNT(*) FROM memorials").fetchone()[0]
        return [self._row_to_memorial(r) for r in rows], total

    # --- Decree ---

    def save_decree(self, decree: Decree) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO decrees
                   (id, memorial_id, action, comment, amended_goal, actor, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decree.id,
                    decree.memorial_id,
                    decree.action,
                    decree.comment,
                    decree.amended_goal,
                    decree.actor,
                    decree.created_at.isoformat(),
                ),
            )

    def list_decrees_by_memorial(self, memorial_id: str) -> list[Decree]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decrees WHERE memorial_id = ? ORDER BY created_at ASC",
                (memorial_id,),
            ).fetchall()
        return [
            Decree(
                id=r["id"],
                memorial_id=r["memorial_id"],
                action=r["action"],
                comment=r["comment"],
                amended_goal=r["amended_goal"],
                actor=r["actor"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # --- Events ---

    def append_event(
        self,
        edict_id: str,
        memorial_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO events
                   (id, edict_id, memorial_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(ULID()),
                    edict_id,
                    memorial_id,
                    event_type,
                    json.dumps(payload, default=str),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_events(self, edict_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "edict_id": r["edict_id"],
                "memorial_id": r["memorial_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_event_stats(self) -> dict:
        """Get event count by type."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT event_type, COUNT(*) as count FROM events GROUP BY event_type ORDER BY count DESC"
            )
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """Get recent events across all edicts."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, edict_id, memorial_id, event_type, payload_json, created_at "
                "FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [
                {
                    "id": row[0],
                    "edict_id": row[1],
                    "memorial_id": row[2],
                    "event_type": row[3],
                    "payload": json.loads(row[4]) if row[4] else {},
                    "created_at": row[5],
                }
                for row in cur.fetchall()
            ]

    def list_persona_events(self, persona_id: str, since_iso: str) -> list[dict]:
        """Events whose payload.persona_id = persona_id AND created_at >= since_iso.

        Note: the project's events table uses columns `created_at` (not `timestamp`)
        and `payload_json` (not `payload`); see CREATE TABLE near the top of this
        file. Output dict normalises `payload_json` -> `payload` so callers see a
        decoded dict, matching the convention used by `get_events` above.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, event_type, edict_id, memorial_id, created_at, payload_json
                FROM events
                WHERE created_at >= ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (since_iso,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if (
                payload.get("persona_id") == persona_id
                or payload.get("assigned_persona_id") == persona_id
                or payload.get("assigned_official") == persona_id
            ):
                r["payload"] = payload
                r.pop("payload_json", None)
                out.append(r)
        return out

    # --- Audit ---

    def get_audit_stats(self, top_n: int = 20) -> dict:
        with self._lock:
            # Query A — global summary
            summary_row = self._conn.execute("""
                SELECT
                    COUNT(*) as total_memorials,
                    COALESCE(SUM(json_extract(usage_json, '$.prompt_tokens')), 0) as total_prompt_tokens,
                    COALESCE(SUM(json_extract(usage_json, '$.completion_tokens')), 0) as total_completion_tokens,
                    COALESCE(SUM(json_extract(usage_json, '$.total_tokens')), 0) as total_tokens,
                    COUNT(CASE WHEN json_extract(audit_json, '$.verdict') = 'pass' THEN 1 END) as audit_pass,
                    COUNT(CASE WHEN json_extract(audit_json, '$.verdict') = 'flag' THEN 1 END) as audit_flag,
                    COUNT(CASE WHEN json_extract(audit_json, '$.verdict') = 'block' THEN 1 END) as audit_block,
                    COUNT(CASE WHEN review_status = 'pending' THEN 1 END) as review_pending,
                    COUNT(CASE WHEN review_status = 'approved' THEN 1 END) as review_approved,
                    COUNT(CASE WHEN review_status = 'rejected' THEN 1 END) as review_rejected
                FROM memorials
            """).fetchone()

            summary = {
                "total_memorials": summary_row["total_memorials"],
                "total_prompt_tokens": summary_row["total_prompt_tokens"],
                "total_completion_tokens": summary_row["total_completion_tokens"],
                "total_tokens": summary_row["total_tokens"],
                "audit_pass": summary_row["audit_pass"],
                "audit_flag": summary_row["audit_flag"],
                "audit_block": summary_row["audit_block"],
                "review_pending": summary_row["review_pending"],
                "review_approved": summary_row["review_approved"],
                "review_rejected": summary_row["review_rejected"],
            }

            # Query B — per-edict token usage
            per_edict_rows = self._conn.execute("""
                SELECT
                    m.edict_id,
                    e.title as edict_title,
                    e.priority,
                    json_extract(e.runtime_json, '$.token_budget') as token_budget,
                    COUNT(*) as memorial_count,
                    COALESCE(SUM(json_extract(m.usage_json, '$.prompt_tokens')), 0) as prompt_tokens,
                    COALESCE(SUM(json_extract(m.usage_json, '$.completion_tokens')), 0) as completion_tokens,
                    COALESCE(SUM(json_extract(m.usage_json, '$.total_tokens')), 0) as total_tokens
                FROM memorials m
                JOIN edicts e ON m.edict_id = e.id
                GROUP BY m.edict_id
                ORDER BY total_tokens DESC
                LIMIT ?
            """, (top_n,)).fetchall()

            per_edict = [
                {
                    "edict_id": r["edict_id"],
                    "edict_title": r["edict_title"],
                    "priority": r["priority"],
                    "token_budget": r["token_budget"],
                    "memorial_count": r["memorial_count"],
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "total_tokens": r["total_tokens"],
                }
                for r in per_edict_rows
            ]

            # Query C — recent audits
            audit_rows = self._conn.execute("""
                SELECT
                    m.id, m.edict_id, e.title as edict_title,
                    m.audit_json, m.review_status, m.completed_at
                FROM memorials m
                JOIN edicts e ON m.edict_id = e.id
                WHERE m.audit_json IS NOT NULL
                ORDER BY COALESCE(m.completed_at, m.created_at) DESC
                LIMIT 20
            """).fetchall()

            recent_audits = []
            for r in audit_rows:
                audit_data = json.loads(r["audit_json"]) if r["audit_json"] else {}
                recent_audits.append({
                    "memorial_id": r["id"],
                    "edict_id": r["edict_id"],
                    "edict_title": r["edict_title"],
                    "verdict": audit_data.get("verdict"),
                    "reasons": audit_data.get("reasons", []),
                    "rules_checked": audit_data.get("rules_checked", 0),
                    "llm_reviewed": audit_data.get("llm_reviewed", False),
                    "review_status": r["review_status"],
                    "completed_at": r["completed_at"],
                })

        return {
            "summary": summary,
            "per_edict": per_edict,
            "recent_audits": recent_audits,
        }

    # --- Memory ---

    def save_memory_entry(self, entry) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO memory_entries
                   (id, persona_id, edict_id, memorial_id, category, content,
                    source, confidence, entity_refs_json, created_at, expires_at, access_level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.persona_id,
                    entry.edict_id,
                    entry.memorial_id,
                    entry.category,
                    entry.content,
                    entry.source,
                    entry.confidence,
                    json.dumps(entry.entity_refs),
                    entry.created_at.isoformat(),
                    entry.expires_at.isoformat() if entry.expires_at else None,
                    entry.access_level,
                ),
            )

    def search_memory(
        self,
        persona_id: str,
        query: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_shared: bool = False,
    ) -> list:
        MemoryEntry = _get_memory_entry()

        # Try FTS5 first if available and query is provided
        if query and getattr(self, "_fts_available", False):
            from tianshu.memory.fts import fts_search
            with self._lock:
                fts_ids = fts_search(self._conn, query, persona_id=persona_id, limit=limit)
            if fts_ids:
                placeholders = ",".join("?" for _ in fts_ids)
                extra_conditions = []
                extra_params: list = list(fts_ids)
                if include_shared:
                    extra_conditions.append(
                        "(persona_id = ? OR access_level IN ('shared', 'court'))"
                    )
                    extra_params.append(persona_id)
                if category:
                    extra_conditions.append("category = ?")
                    extra_params.append(category)
                where_extra = (
                    f" AND {' AND '.join(extra_conditions)}" if extra_conditions else ""
                )
                with self._lock:
                    rows = self._conn.execute(
                        f"SELECT * FROM memory_entries WHERE id IN ({placeholders}){where_extra} ORDER BY created_at DESC LIMIT ?",
                        (*extra_params, limit),
                    ).fetchall()
                return [self._row_to_memory_entry(r) for r in rows]

        # Fallback to LIKE search
        conditions = []
        params: list = []

        if include_shared:
            conditions.append("(persona_id = ? OR access_level IN ('shared', 'court'))")
            params.append(persona_id)
        else:
            conditions.append("persona_id = ?")
            params.append(persona_id)

        if category:
            conditions.append("category = ?")
            params.append(category)
        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memory_entries{where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_memory_entry(r) for r in rows]

    def list_memory_by_persona(self, persona_id: str, limit: int = 50) -> list:
        MemoryEntry = _get_memory_entry()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_entries WHERE persona_id = ? ORDER BY created_at DESC LIMIT ?",
                (persona_id, limit),
            ).fetchall()
        return [self._row_to_memory_entry(r) for r in rows]

    def delete_memory_entry(self, entry_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
            )
            return cursor.rowcount > 0

    def delete_memory_entries_batch(self, entry_ids: list[str]) -> int:
        """Delete multiple memory entries by ID. Returns count of deleted rows."""
        if not entry_ids:
            return 0
        placeholders = ",".join("?" for _ in entry_ids)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"DELETE FROM memory_entries WHERE id IN ({placeholders})",
                entry_ids,
            )
            return cursor.rowcount

    @staticmethod
    def _row_to_memory_entry(row: sqlite3.Row):
        MemoryEntry = _get_memory_entry()
        return MemoryEntry(
            id=row["id"],
            persona_id=row["persona_id"],
            edict_id=row["edict_id"],
            memorial_id=row["memorial_id"],
            category=row["category"],
            content=row["content"],
            source=row["source"],
            confidence=row["confidence"],
            entity_refs=json.loads(row["entity_refs_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            access_level=row["access_level"],
        )

    # --- Cost Ledger ---

    def save_cost_record(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO cost_ledger
                   (id, edict_id, memorial_id, provider_name, model,
                    prompt_tokens, completion_tokens, total_tokens, cost_cny, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.edict_id,
                    record.memorial_id,
                    record.provider_name,
                    record.model,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    record.cost_cny,
                    record.created_at.isoformat(),
                ),
            )

    def get_cost_summary(
        self,
        period: str | None = None,
        edict_id: str | None = None,
    ) -> dict:
        conditions: list[str] = []
        params: list = []
        if edict_id:
            conditions.append("edict_id = ?")
            params.append(edict_id)
        if period == "day":
            conditions.append("date(created_at) = date('now')")
        elif period == "week":
            conditions.append("date(created_at) >= date('now', '-7 days')")
        elif period == "month":
            conditions.append("date(created_at) >= date('now', '-30 days')")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            row = self._conn.execute(
                f"""SELECT
                    COUNT(*) as total_records,
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(cost_cny), 0.0) as total_cost_cny
                FROM cost_ledger{where}""",
                params,
            ).fetchone()
        return {
            "total_records": row["total_records"],
            "total_prompt_tokens": row["total_prompt_tokens"],
            "total_completion_tokens": row["total_completion_tokens"],
            "total_tokens": row["total_tokens"],
            "total_cost_cny": row["total_cost_cny"],
        }

    def list_cost_records(
        self,
        edict_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        conditions: list[str] = []
        params: list = []
        if edict_id:
            conditions.append("edict_id = ?")
            params.append(edict_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM cost_ledger{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM cost_ledger{where}", params
            ).fetchone()[0]
        return [dict(r) for r in rows], total

    def get_budget(self, scope: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_budget(
        self,
        scope: str,
        budget_cny: float,
        period: str = "monthly",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE cost_budgets SET budget_cny = ?, period = ? WHERE scope = ?",
                    (budget_cny, period, scope),
                )
            else:
                self._conn.execute(
                    """INSERT INTO cost_budgets (id, scope, budget_cny, period, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(ULID()), scope, budget_cny, period, now),
                )

    def update_budget_spent(self, scope: str, amount_cny: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE cost_budgets SET spent_cny = spent_cny + ? WHERE scope = ?",
                (amount_cny, scope),
            )

    # --- LLM Configs ---

    def save_llm_config(self, config: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO llm_configs
                   (name, model, api_key, api_base, max_retries, temperature,
                    top_p, max_tokens, enabled, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    config["name"],
                    config["model"],
                    config["api_key"],
                    config.get("api_base", ""),
                    config.get("max_retries", 3),
                    config.get("temperature", 0.7),
                    config.get("top_p", 1.0),
                    config.get("max_tokens", 4096),
                    1 if config.get("enabled", True) else 0,
                    1 if config.get("is_active", False) else 0,
                    config.get("created_at", datetime.now(UTC).isoformat()),
                ),
            )

    def list_llm_configs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM llm_configs ORDER BY is_active DESC, name ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_llm_config(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM llm_configs WHERE name = ?", (name,)
            ).fetchone()
        return dict(row) if row else None

    def delete_llm_config(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM llm_configs WHERE name = ?", (name,)
            )
            return cursor.rowcount > 0

    def set_active_llm_config(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE llm_configs SET is_active = 0")
            self._conn.execute(
                "UPDATE llm_configs SET is_active = 1 WHERE name = ?", (name,)
            )

    # --- Providers ---

    def save_provider(self, provider: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO providers
                   (name, model, api_base, capabilities_json, rpm_limit, tpm_limit,
                    rpm_current, tpm_current, rpm_window_start, status, priority,
                    cost_per_1k_prompt, cost_per_1k_completion, cost_per_1k_cache_read,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider["name"],
                    provider["model"],
                    provider.get("api_base"),
                    json.dumps(provider.get("capabilities", [])),
                    provider.get("rpm_limit"),
                    provider.get("tpm_limit"),
                    provider.get("rpm_current", 0),
                    provider.get("tpm_current", 0),
                    provider.get("rpm_window_start"),
                    provider.get("status", "active"),
                    provider.get("priority", 100),
                    provider.get("cost_per_1k_prompt"),
                    provider.get("cost_per_1k_completion"),
                    provider.get("cost_per_1k_cache_read"),
                    provider.get("created_at", datetime.now(UTC).isoformat()),
                ),
            )

    def get_provider(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM providers WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["capabilities"] = json.loads(d.pop("capabilities_json", "[]"))
        return d

    def list_providers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM providers ORDER BY priority ASC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["capabilities"] = json.loads(d.pop("capabilities_json", "[]"))
            result.append(d)
        return result

    def delete_provider(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM providers WHERE name = ?", (name,)
            )
            return cursor.rowcount > 0

    def update_provider(self, name: str, updates: dict) -> None:
        sets: list[str] = []
        params: list = []
        for key, value in updates.items():
            if key == "capabilities":
                sets.append("capabilities_json = ?")
                params.append(json.dumps(value))
            elif key in ("model", "api_base", "status", "rpm_limit", "tpm_limit",
                         "priority", "cost_per_1k_prompt", "cost_per_1k_completion",
                         "cost_per_1k_cache_read"):
                sets.append(f"{key} = ?")
                params.append(value)
        if not sets:
            return
        params.append(name)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE providers SET {', '.join(sets)} WHERE name = ?", params
            )

    # --- Plugins ---

    def save_plugin(self, plugin: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO plugins
                   (name, version, manifest_json, status, sha256, installed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plugin["name"],
                    plugin.get("version", "0.0.0"),
                    json.dumps(plugin.get("manifest", {})),
                    plugin.get("status", "active"),
                    plugin.get("sha256"),
                    plugin.get("installed_at", now),
                    now,
                ),
            )

    def list_plugins(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM plugins ORDER BY name ASC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
            result.append(d)
        return result

    def get_plugin(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM plugins WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
        return d

    def update_plugin_status(self, name: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE plugins SET status = ?, updated_at = ? WHERE name = ?",
                (status, datetime.now(UTC).isoformat(), name),
            )

    # --- DAG Executions ---

    def save_dag_execution(self, execution: DAGExecution) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO dag_executions
                   (id, edict_id, plan_json, status, root_memorial_id,
                    max_concurrency, created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution.id,
                    execution.edict_id,
                    execution.plan_json,
                    execution.status,
                    execution.root_memorial_id,
                    execution.max_concurrency,
                    execution.created_at.isoformat(),
                    execution.completed_at.isoformat() if execution.completed_at else None,
                ),
            )
            for node in execution.nodes:
                node.dag_execution_id = execution.id
                self._conn.execute(
                    """INSERT INTO dag_nodes
                       (node_id, dag_execution_id, description, depends_on_json,
                        status, assigned_official, assigned_worker,
                        tools_required_json, memorial_id, checkpoint_json,
                        started_at, completed_at, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        node.node_id,
                        execution.id,
                        node.description,
                        json.dumps(node.depends_on),
                        node.status.value,
                        node.assigned_official,
                        node.assigned_worker,
                        json.dumps(node.tools_required),
                        node.memorial_id,
                        node.checkpoint_json,
                        node.started_at.isoformat() if node.started_at else None,
                        node.completed_at.isoformat() if node.completed_at else None,
                        node.error,
                    ),
                )

    def get_dag_execution(self, dag_id: str) -> DAGExecution | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM dag_executions WHERE id = ?", (dag_id,)
            ).fetchone()
            if not row:
                return None
            nodes = self._get_dag_nodes_internal(dag_id)
        return self._row_to_dag_execution(row, nodes)

    def get_dag_by_edict(self, edict_id: str) -> DAGExecution | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM dag_executions WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
                (edict_id,),
            ).fetchone()
            if not row:
                return None
            nodes = self._get_dag_nodes_internal(row["id"])
        return self._row_to_dag_execution(row, nodes)

    def get_dag_nodes(self, dag_execution_id: str) -> list[DAGNode]:
        with self._lock:
            return self._get_dag_nodes_internal(dag_execution_id)

    def _get_dag_nodes_internal(self, dag_execution_id: str) -> list[DAGNode]:
        rows = self._conn.execute(
            "SELECT * FROM dag_nodes WHERE dag_execution_id = ?",
            (dag_execution_id,),
        ).fetchall()
        return [self._row_to_dag_node(r) for r in rows]

    def update_dag_execution_status(
        self, dag_id: str, status: str, completed_at: datetime | None = None,
    ) -> None:
        with self._lock, self._conn:
            if completed_at:
                self._conn.execute(
                    "UPDATE dag_executions SET status = ?, completed_at = ? WHERE id = ?",
                    (status, completed_at.isoformat(), dag_id),
                )
            else:
                self._conn.execute(
                    "UPDATE dag_executions SET status = ? WHERE id = ?",
                    (status, dag_id),
                )

    def update_dag_node_status(
        self,
        dag_execution_id: str,
        node_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            if status == "running":
                self._conn.execute(
                    "UPDATE dag_nodes SET status = ?, started_at = ? WHERE dag_execution_id = ? AND node_id = ?",
                    (status, now, dag_execution_id, node_id),
                )
            elif status in ("completed", "failed", "cancelled"):
                self._conn.execute(
                    "UPDATE dag_nodes SET status = ?, completed_at = ?, error = ? WHERE dag_execution_id = ? AND node_id = ?",
                    (status, now, error, dag_execution_id, node_id),
                )
            else:
                self._conn.execute(
                    "UPDATE dag_nodes SET status = ?, error = ? WHERE dag_execution_id = ? AND node_id = ?",
                    (status, error, dag_execution_id, node_id),
                )

    def update_dag_node_checkpoint(
        self, dag_execution_id: str, node_id: str, checkpoint_json: str | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE dag_nodes SET checkpoint_json = ? WHERE dag_execution_id = ? AND node_id = ?",
                (checkpoint_json, dag_execution_id, node_id),
            )

    def save_dag_node(self, node: DAGNode) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO dag_nodes
                   (node_id, dag_execution_id, description, depends_on_json,
                    status, assigned_official, assigned_worker,
                    tools_required_json, memorial_id, checkpoint_json,
                    started_at, completed_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node.node_id,
                    node.dag_execution_id,
                    node.description,
                    json.dumps(node.depends_on),
                    node.status.value,
                    node.assigned_official,
                    node.assigned_worker,
                    json.dumps(node.tools_required),
                    node.memorial_id,
                    node.checkpoint_json,
                    node.started_at.isoformat() if node.started_at else None,
                    node.completed_at.isoformat() if node.completed_at else None,
                    node.error,
                ),
            )

    @staticmethod
    def _row_to_dag_execution(row: sqlite3.Row, nodes: list[DAGNode]) -> DAGExecution:
        return DAGExecution(
            id=row["id"],
            edict_id=row["edict_id"],
            plan_json=row["plan_json"],
            status=row["status"],
            root_memorial_id=row["root_memorial_id"],
            max_concurrency=row["max_concurrency"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            nodes=nodes,
        )

    @staticmethod
    def _row_to_dag_node(row: sqlite3.Row) -> DAGNode:
        return DAGNode(
            node_id=row["node_id"],
            dag_execution_id=row["dag_execution_id"],
            description=row["description"],
            depends_on=json.loads(row["depends_on_json"]),
            status=DAGNodeStatus(row["status"]),
            assigned_official=row["assigned_official"],
            assigned_worker=row["assigned_worker"],
            tools_required=json.loads(row["tools_required_json"]),
            memorial_id=row["memorial_id"],
            checkpoint_json=row["checkpoint_json"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            error=row["error"],
        )

    # --- Scheduler Jobs ---

    def save_scheduler_job(
        self,
        job_id: str,
        edict_id: str,
        schedule_type: str,
        cron_expr: str | None = None,
        next_run: datetime | None = None,
        interval_seconds: int | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO scheduler_jobs
                   (job_id, edict_id, schedule_type, cron_expr, next_run, status, created_at, interval_seconds)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    job_id,
                    edict_id,
                    schedule_type,
                    cron_expr,
                    next_run.isoformat() if next_run else None,
                    datetime.now(UTC).isoformat(),
                    interval_seconds,
                ),
            )

    def update_scheduler_job_next_run(self, job_id: str, next_run: datetime | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET next_run = ? WHERE job_id = ?",
                (next_run.isoformat() if next_run else None, job_id),
            )

    def set_scheduler_job_status(self, job_id: str, status: str) -> None:
        """Set a job's status (active | paused | cancelled)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )

    def get_scheduler_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduler_jobs WHERE job_id = ?", (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_scheduler_job(self, job_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET status = 'cancelled' WHERE job_id = ?",
                (job_id,),
            )

    def list_active_scheduler_jobs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduler_jobs WHERE status = 'active' ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_scheduler_jobs(
        self, statuses: tuple[str, ...] = ("active", "paused"),
    ) -> list[dict]:
        """List scheduler jobs filtered by status (default: active + paused)."""
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM scheduler_jobs WHERE status IN ({placeholders}) "
                "ORDER BY created_at ASC",
                tuple(statuses),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Idempotency ---

    def find_edict_by_idempotency_key(
        self, submitter: str | None, idempotency_key: str,
    ) -> Edict | None:
        """Find an existing edict by (submitter, idempotency_key) pair."""
        with self._lock:
            if submitter:
                row = self._conn.execute(
                    "SELECT * FROM edicts WHERE idempotency_key = ? AND submitter = ?",
                    (idempotency_key, submitter),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM edicts WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
        return self._row_to_edict(row) if row else None

    # --- Edict active memorial check ---

    def has_active_memorials(self, edict_id: str) -> bool:
        """Check if edict has any SUBMITTED or RUNNING memorials."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM memorials WHERE edict_id = ? AND status IN ('submitted', 'running')",
                (edict_id,),
            ).fetchone()
        return row[0] > 0

    # --- Persona Stats (Phase 3.12) ---

    def get_persona_stats(self, persona_id: str) -> dict:
        with self._lock:
            row = self._conn.execute("""
                SELECT
                    COUNT(*) as total_executions,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled,
                    COALESCE(SUM(json_extract(usage_json, '$.total_tokens')), 0) as total_tokens,
                    COALESCE(AVG(
                        CASE WHEN completed_at IS NOT NULL AND started_at IS NOT NULL
                        THEN (julianday(completed_at) - julianday(started_at)) * 86400
                        END
                    ), 0.0) as avg_duration_seconds
                FROM memorials
                WHERE persona_id = ?
            """, (persona_id,)).fetchone()

        total = row["total_executions"] or 0
        completed = row["completed"] or 0
        success_rate = (completed / total * 100) if total > 0 else 0.0
        total_tokens = row["total_tokens"] or 0
        avg_tokens = (total_tokens / total) if total > 0 else 0.0

        # Cost from cost_ledger (join via memorial_id)
        cost_row = self._conn.execute("""
            SELECT COALESCE(SUM(cl.cost_cny), 0.0) as total_cost
            FROM cost_ledger cl
            JOIN memorials m ON cl.memorial_id = m.id
            WHERE m.persona_id = ?
        """, (persona_id,)).fetchone()

        return {
            "total_executions": total,
            "completed": completed,
            "failed": row["failed"] or 0,
            "cancelled": row["cancelled"] or 0,
            "success_rate": round(success_rate, 2),
            "total_tokens": total_tokens,
            "avg_tokens_per_execution": round(avg_tokens, 1),
            "total_cost_cny": round(cost_row["total_cost"], 6) if cost_row else 0.0,
            "avg_duration_seconds": round(row["avg_duration_seconds"] or 0.0, 2),
        }

    # --- Department CRUD ---

    def save_department(self, dept: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO departments (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (
                    dept["id"],
                    dept["name"],
                    dept.get("description", ""),
                    dept.get("created_at", now),
                ),
            )

    def list_departments(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM departments ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_department(self, dept_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM departments WHERE id = ?", (dept_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_department(self, dept_id: str, **fields) -> None:
        allowed = {"name", "description"}
        sets: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.append(dept_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE departments SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def delete_department(self, dept_id: str) -> bool:
        """Delete department. Refuses if any persona references it."""
        with self._lock:
            ref_count = self._conn.execute(
                "SELECT COUNT(*) FROM personas WHERE department = ?", (dept_id,)
            ).fetchone()[0]
            if ref_count > 0:
                return False
            with self._conn:
                cursor = self._conn.execute(
                    "DELETE FROM departments WHERE id = ?", (dept_id,)
                )
                return cursor.rowcount > 0

    # --- Persona CRUD ---

    def save_persona(self, persona: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO personas
                   (id, name, department, title, tools_allowed, tools_denied,
                    skills_allowed, tool_tier_max, can_delegate, memory_global_read, delegates_to,
                    soul_path, role_path, llm_config_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    persona["id"],
                    persona["name"],
                    persona["department"],
                    persona.get("title"),
                    json.dumps(persona.get("tools_allowed", [])),
                    json.dumps(persona.get("tools_denied", [])),
                    json.dumps(persona.get("skills_allowed", [])),
                    persona.get("tool_tier_max", 0),
                    int(persona.get("can_delegate", False)),
                    int(persona.get("memory_global_read", False)),
                    json.dumps(persona.get("delegates_to", [])),
                    persona.get("soul_path"),
                    persona.get("role_path"),
                    persona.get("llm_config_name"),
                    persona.get("created_at", now),
                    now,
                ),
            )

    def list_personas(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM personas ORDER BY department, name"
            ).fetchall()
        return [self._row_to_persona_dict(r) for r in rows]

    def get_persona(self, persona_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM personas WHERE id = ?", (persona_id,)
            ).fetchone()
        return self._row_to_persona_dict(row) if row else None

    def update_persona(self, persona_id: str, **fields) -> None:
        allowed = {
            "name", "department", "title", "tools_allowed", "tools_denied",
            "skills_allowed", "tool_tier_max", "can_delegate", "memory_global_read", "delegates_to",
            "soul_path", "role_path", "llm_config_name",
        }
        sets: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("tools_allowed", "tools_denied", "skills_allowed", "delegates_to"):
                sets.append(f"{key} = ?")
                params.append(json.dumps(value))
            elif key in ("can_delegate", "memory_global_read"):
                sets.append(f"{key} = ?")
                params.append(int(value))
            else:
                sets.append(f"{key} = ?")
                params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(datetime.now(UTC).isoformat())
        params.append(persona_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE personas SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def delete_persona(self, persona_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM personas WHERE id = ?", (persona_id,)
            )
            return cursor.rowcount > 0

    # --- Persona Metrics / Profile Synthesis ---

    def try_acquire_synthesis_lock(
        self, persona_id: str, stale_timeout_sec: int = 600
    ) -> bool:
        """Return True if lock acquired. Reclaims stale locks > stale_timeout_sec."""
        now_iso = datetime.now(UTC).isoformat()
        stale_cutoff = (
            datetime.now(UTC) - timedelta(seconds=stale_timeout_sec)
        ).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE persona_metrics
                SET synthesis_in_progress=1, synthesis_started_at=?
                WHERE persona_id=?
                  AND (synthesis_in_progress=0
                       OR synthesis_started_at < ?)
                """,
                (now_iso, persona_id, stale_cutoff),
            )
            if cur.rowcount > 0:
                self._conn.commit()
                return True
            # ensure row exists
            self._conn.execute(
                "INSERT OR IGNORE INTO persona_metrics(persona_id) VALUES (?)",
                (persona_id,),
            )
            self._conn.commit()
            # retry once
            cur = self._conn.execute(
                """
                UPDATE persona_metrics
                SET synthesis_in_progress=1, synthesis_started_at=?
                WHERE persona_id=?
                  AND (synthesis_in_progress=0
                       OR synthesis_started_at < ?)
                """,
                (now_iso, persona_id, stale_cutoff),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def release_synthesis_lock(self, persona_id: str) -> None:
        """Release lock and reset throttle counter after synthesis cycle (success or degraded)."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE persona_metrics
                SET synthesis_in_progress=0, synthesis_started_at=NULL,
                    tasks_since_last_synthesis=0
                WHERE persona_id=?
                """,
                (persona_id,),
            )
            self._conn.commit()

    def last_activity_at(self) -> str | None:
        """Most recent event timestamp (ISO) for idle gating; None if no events.

        Execution events carry an edict_id and are persisted, so MAX(created_at)
        across the events table approximates the last real agent activity.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) AS ts FROM events"
            ).fetchone()
        return row["ts"] if row and row["ts"] else None

    def increment_persona_task_counter(self, persona_id: str) -> int:
        """Increment tasks_since_last_synthesis by 1, create row if missing; return new value."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO persona_metrics(persona_id) VALUES (?)",
                (persona_id,),
            )
            self._conn.execute(
                """
                UPDATE persona_metrics
                SET tasks_since_last_synthesis = tasks_since_last_synthesis + 1
                WHERE persona_id=?
                """,
                (persona_id,),
            )
            cur = self._conn.execute(
                "SELECT tasks_since_last_synthesis FROM persona_metrics WHERE persona_id=?",
                (persona_id,),
            )
            row = cur.fetchone()
            self._conn.commit()
            return int(row[0]) if row else 0

    @staticmethod
    def _row_to_persona_dict(row: sqlite3.Row) -> dict:
        keys = row.keys()
        return {
            "id": row["id"],
            "name": row["name"],
            "department": row["department"],
            "title": row["title"] if "title" in keys else None,
            "tools_allowed": json.loads(row["tools_allowed"]),
            "tools_denied": json.loads(row["tools_denied"]),
            "skills_allowed": json.loads(row["skills_allowed"]) if "skills_allowed" in keys else [],
            "tool_tier_max": row["tool_tier_max"],
            "can_delegate": bool(row["can_delegate"]),
            "memory_global_read": bool(row["memory_global_read"]) if "memory_global_read" in keys else False,
            "delegates_to": json.loads(row["delegates_to"]),
            "soul_path": row["soul_path"],
            "role_path": row["role_path"],
            "llm_config_name": row["llm_config_name"] if "llm_config_name" in keys else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- Universes (平行位面) ---

    def save_universe(self, uni: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO universes
                   (id, name, parent_universe_id, status, origin,
                    mutation_reason, description, fitness_json, code_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uni["id"], uni["name"], uni.get("parent_universe_id"),
                    uni["status"], uni["origin"], uni.get("mutation_reason"),
                    uni.get("description", ""),
                    json.dumps(uni.get("fitness", {}), ensure_ascii=False),
                    uni.get("code_ref"),
                    uni["created_at"],
                ),
            )

    def get_universe(self, universe_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM universes WHERE id = ?", (universe_id,)
            ).fetchone()
        return self._row_to_universe(row) if row else None

    def list_universes(self, *, include_archived: bool = True) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM universes"
            if not include_archived:
                sql += " WHERE status != 'archived'"
            sql += " ORDER BY created_at DESC"
            return [self._row_to_universe(r) for r in self._conn.execute(sql).fetchall()]

    def get_champion_universe(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM universes WHERE status = 'champion'"
            ).fetchone()
        return self._row_to_universe(row) if row else None

    def set_universe_status(self, universe_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE universes SET status = ? WHERE id = ?", (status, universe_id)
            )

    def update_universe_fitness(self, universe_id: str, fitness: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE universes SET fitness_json = ? WHERE id = ?",
                (json.dumps(fitness, ensure_ascii=False), universe_id),
            )

    def set_memorial_feedback(self, memorial_id: str, score: int) -> None:
        """设置某 memorial 的显式反馈分（-1/0/1）。"""
        score = max(-1, min(1, int(score)))
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memorials SET feedback_score = ? WHERE id = ?",
                (score, memorial_id),
            )

    def universe_memorial_stats(self, universe_id: str) -> dict:
        """聚合某位面下 memorial 的成功/失败/重试/成本/审计/反馈。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, attempt, usage_json, audit_json, feedback_score "
                "FROM memorials WHERE universe_id = ?",
                (universe_id,),
            ).fetchall()
        total = len(rows)
        success = sum(1 for r in rows if r["status"] in ("completed", "approved"))
        retries = sum(max(0, (r["attempt"] or 1) - 1) for r in rows)
        feedback = sum((r["feedback_score"] or 0) for r in rows)
        audited = 0
        audit_pass = 0
        cost = 0.0
        for r in rows:
            try:
                u = json.loads(r["usage_json"] or "{}")
                cost += float(u.get("cost_cny", 0.0) or 0.0)
            except (ValueError, TypeError):
                pass
            aj = r["audit_json"]
            if aj:
                audited += 1
                try:
                    a = json.loads(aj)
                    if _audit_passed(a):
                        audit_pass += 1
                except (ValueError, TypeError):
                    pass
        return {
            "total": total, "success": success, "retries": retries,
            "audited": audited, "audit_pass": audit_pass,
            "cost": round(cost, 6), "feedback": feedback,
        }

    @staticmethod
    def _row_to_universe(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "parent_universe_id": row["parent_universe_id"],
            "status": row["status"],
            "origin": row["origin"],
            "mutation_reason": row["mutation_reason"],
            "description": row["description"],
            "fitness": json.loads(row["fitness_json"] or "{}"),
            "code_ref": row["code_ref"],
            "created_at": row["created_at"],
        }

    # --- Memorials by Persona ---

    def list_memorials_by_persona(
        self,
        persona_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return memorials grouped by edict for a persona.

        For 'bingbu' (default executor), also includes memorials with NULL persona_id
        to cover legacy data created before persona_id tracking was added.
        """
        if persona_id == "bingbu":
            join_where = "(m.persona_id = ? OR m.persona_id IS NULL)"
            count_where = "(persona_id = ? OR persona_id IS NULL)"
        else:
            join_where = "m.persona_id = ?"
            count_where = "persona_id = ?"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT m.*, e.title as edict_title, e.goal as edict_goal, e.status as edict_status
                   FROM memorials m
                   JOIN edicts e ON m.edict_id = e.id
                   WHERE {join_where}
                   ORDER BY m.created_at DESC
                   LIMIT ? OFFSET ?""",
                (persona_id, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM memorials WHERE {count_where}",
                (persona_id,),
            ).fetchone()[0]

        # Group by edict_id
        edicts_map: dict[str, dict] = {}
        for r in rows:
            eid = r["edict_id"]
            if eid not in edicts_map:
                edicts_map[eid] = {
                    "edict_id": eid,
                    "edict_title": r["edict_title"],
                    "edict_goal": r["edict_goal"],
                    "edict_status": r["edict_status"],
                    "memorials": [],
                }
            edicts_map[eid]["memorials"].append({
                "id": r["id"],
                "instruction": r["instruction"],
                "status": r["status"],
                "result": r["result"],
                "summary": r["summary"],
                "error": r["error"],
                "created_at": r["created_at"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
            })

        return list(edicts_map.values()), total

    # --- Helpers ---

    @staticmethod
    def _row_to_edict(row: sqlite3.Row) -> Edict:
        # Handle optional Phase 1 columns gracefully
        keys = row.keys()

        schedule = EdictSchedule()
        if "schedule_json" in keys and row["schedule_json"]:
            try:
                schedule = EdictSchedule.model_validate_json(row["schedule_json"])
            except Exception:
                pass

        dispatch = None
        if "dispatch_json" in keys and row["dispatch_json"]:
            try:
                dispatch = EdictDispatch.model_validate_json(row["dispatch_json"])
            except Exception:
                pass

        runtime = EdictRuntime()
        if "runtime_json" in keys and row["runtime_json"]:
            try:
                runtime = EdictRuntime.model_validate_json(row["runtime_json"])
            except Exception:
                pass

        constraints = []
        if "constraints_json" in keys and row["constraints_json"]:
            try:
                constraints = json.loads(row["constraints_json"])
            except Exception:
                pass

        metadata = {}
        if "metadata_json" in keys and row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except Exception:
                pass

        acceptance = None
        if "acceptance_json" in keys and row["acceptance_json"]:
            try:
                acceptance = AcceptanceCriteria.model_validate_json(row["acceptance_json"])
            except Exception as exc:
                logger.warning(
                    "Failed to deserialize acceptance_json for edict %s: %s",
                    row["id"], exc,
                )

        return Edict(
            id=row["id"],
            title=row["title"] if "title" in keys else "",
            goal=row["goal"],
            context=row["context"],
            status=EdictStatus(row["status"]) if "status" in keys else EdictStatus.OPEN,
            created_at=datetime.fromisoformat(row["created_at"]),
            idempotency_key=row["idempotency_key"] if "idempotency_key" in keys else None,
            source=row["source"] if "source" in keys else "api",
            submitter=row["submitter"] if "submitter" in keys else None,
            priority=row["priority"] if "priority" in keys else "normal",
            review_policy=row["review_policy"] if "review_policy" in keys else "never",
            output_format=row["output_format"] if "output_format" in keys else None,
            assigned_persona_id=row["assigned_persona_id"] if "assigned_persona_id" in keys else None,
            planner_persona_id=row["planner_persona_id"] if "planner_persona_id" in keys else None,
            plan_review=bool(row["plan_review"]) if "plan_review" in keys else False,
            acceptance=acceptance,
            execution_profile=row["execution_profile"] if "execution_profile" in keys else "foreground",
            constraints=constraints,
            schedule=schedule,
            dispatch=dispatch,
            runtime=runtime,
            metadata=metadata,
        )

    @staticmethod
    def _row_to_memorial(row: sqlite3.Row) -> Memorial:
        keys = row.keys()
        usage_data = json.loads(row["usage_json"]) if row["usage_json"] else {}

        audit = None
        if "audit_json" in keys and row["audit_json"]:
            try:
                audit = AuditResult.model_validate_json(row["audit_json"])
            except Exception:
                pass

        return Memorial(
            id=row["id"],
            edict_id=row["edict_id"],
            instruction=row["instruction"] if "instruction" in keys else None,
            status=TaskStatus(row["status"]),
            summary=row["summary"],
            result=row["result"],
            usage=UsageSummary(**usage_data),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            attempt=row["attempt"] if "attempt" in keys else 1,
            parent_memorial_id=row["parent_memorial_id"] if "parent_memorial_id" in keys else None,
            review_status=row["review_status"] if "review_status" in keys else "not_required",
            audit=audit,
            dag_node_id=row["dag_node_id"] if "dag_node_id" in keys else None,
            persona_id=row["persona_id"] if "persona_id" in keys else None,
            runtime_override=Storage._parse_runtime_override(row, keys),
            acceptance_override=Storage._parse_acceptance_override(row, keys),
            reasoning_content=(
                row["reasoning_content"]
                if "reasoning_content" in keys
                else None
            ),
            final_output=(
                row["final_output"]
                if "final_output" in keys
                else None
            ),
            universe_id=row["universe_id"] if "universe_id" in keys else None,
            feedback_score=row["feedback_score"] if "feedback_score" in keys else 0,
        )

    @staticmethod
    def _parse_runtime_override(row: sqlite3.Row, keys) -> dict | None:
        if "runtime_override_json" not in keys or not row["runtime_override_json"]:
            return None
        try:
            data = json.loads(row["runtime_override_json"])
            return data if isinstance(data, dict) else None
        except Exception:
            logger.warning("invalid runtime_override_json for memorial %s", row["id"])
            return None

    @staticmethod
    def _parse_acceptance_override(row: sqlite3.Row, keys) -> "AcceptanceCriteria | None":
        if "acceptance_override_json" not in keys or not row["acceptance_override_json"]:
            return None
        try:
            return AcceptanceCriteria.model_validate_json(row["acceptance_override_json"])
        except Exception:
            logger.warning("invalid acceptance_override_json for memorial %s", row["id"])
            return None

    @staticmethod
    def _memorial_to_params(m: Memorial) -> tuple:
        return (
            m.id,
            m.edict_id,
            m.instruction,
            m.status.value,
            m.summary,
            m.result,
            m.usage.model_dump_json(),
            m.error,
            m.created_at.isoformat(),
            m.started_at.isoformat() if m.started_at else None,
            m.completed_at.isoformat() if m.completed_at else None,
            m.attempt,
            m.parent_memorial_id,
            m.review_status,
            m.audit.model_dump_json() if m.audit else None,
            json.dumps([a.model_dump() for a in m.artifacts], default=str),
            json.dumps([t.model_dump() for t in m.timeline], default=str),
            m.dag_node_id,
            m.persona_id,
            json.dumps(m.runtime_override) if m.runtime_override else None,
            m.acceptance_override.model_dump_json() if m.acceptance_override else None,
            m.reasoning_content,
            m.final_output,
            m.universe_id,
        )

    def insert_credential(
        self,
        *,
        cred_id: str,
        name: str,
        host_pattern: str,
        header_template: str,
        extra_headers_json: str,
        encrypted_value: bytes,
        now_iso: str,
        kind: str = "edict_auth",
        provider_name: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO network_credentials
                   (id, name, host_pattern, header_template, extra_headers,
                    encrypted_value, created_at, updated_at, kind, provider_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cred_id, name, host_pattern, header_template,
                 extra_headers_json, encrypted_value, now_iso, now_iso,
                 kind, provider_name),
            )

    def list_credentials(self, kind: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if kind:
                cur = self._conn.execute(
                    "SELECT * FROM network_credentials "
                    "WHERE deleted_at IS NULL AND kind=? "
                    "ORDER BY name",
                    (kind,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM network_credentials "
                    "WHERE deleted_at IS NULL "
                    "ORDER BY name"
                )
            return cur.fetchall()

    def get_credential_by_id(self, cred_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM network_credentials WHERE id=? AND deleted_at IS NULL",
                (cred_id,),
            )
            return cur.fetchone()

    def find_credentials_by_host(self, host: str) -> list[sqlite3.Row]:
        """返回所有可能匹配此 host 的 edict_auth 凭证（literal + 通配）。
        强制 kind='edict_auth' 过滤 — engine_provider key 永不参与 host 匹配，
        从根源隔离 LLM 可访问面。enabled=0 视为未配置，跳过。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM network_credentials "
                "WHERE deleted_at IS NULL AND kind='edict_auth' "
                "AND enabled = 1 "
                "AND (host_pattern=? OR host_pattern LIKE '*.%')",
                (host,),
            )
            return cur.fetchall()

    def find_credentials_by_provider(self, provider_name: str) -> sqlite3.Row | None:
        """disabled 的 provider 凭证视为未配置，返回 None 让 resolve 回落 env。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM network_credentials "
                "WHERE deleted_at IS NULL AND kind='engine_provider' "
                "AND enabled = 1 "
                "AND provider_name=?",
                (provider_name,),
            )
            return cur.fetchone()

    def update_credential(
        self,
        cred_id: str,
        *,
        encrypted_value: bytes | None = None,
        extra_headers_json: str | None = None,
        enabled: bool | None = None,
        now_iso: str,
    ) -> None:
        sets = ["updated_at=?"]
        params: list[object] = [now_iso]
        if encrypted_value is not None:
            sets.append("encrypted_value=?")
            params.append(encrypted_value)
        if extra_headers_json is not None:
            sets.append("extra_headers=?")
            params.append(extra_headers_json)
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
        params.append(cred_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE network_credentials SET {', '.join(sets)} WHERE id=?",
                params,
            )

    def mark_credential_used(self, cred_id: str, now_iso: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE network_credentials SET last_used_at=? WHERE id=?",
                (now_iso, cred_id),
            )

    def soft_delete_credential(self, cred_id: str, now_iso: str) -> None:
        # 同时 append 后缀让出 name（UNIQUE）位置，防止用户重建同名凭证时 IntegrityError
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE network_credentials "
                "SET deleted_at=?, name = name || '__deleted_' || id "
                "WHERE id=? AND deleted_at IS NULL",
                (now_iso, cred_id),
            )

    def find_edicts_referencing_host(self, host_pattern: str) -> list[str]:
        """返回引用此 host 的未结束 Edict id 列表。仅匹配精确 host，通配模式统一视为精确。

        用于凭证删除前置检查 — 有引用即阻止删除。
        """
        with self._lock:
            # edicts 表的 runtime 字段存 JSON；在 JSON 里 grep host
            # 约束：只搜 runtime_json LIKE 包含 host 的，Python 侧再确认
            cur = self._conn.execute(
                """SELECT id, runtime_json FROM edicts
                   WHERE status = 'open'
                   AND runtime_json LIKE ?""",
                (f"%{host_pattern}%",),
            )
            hits: list[str] = []
            for row in cur.fetchall():
                rjson = row["runtime_json"] or "{}"
                try:
                    runtime = json.loads(rjson)
                except Exception:
                    continue
                hosts = runtime.get("api_request_hosts") or []
                write_hosts = runtime.get("api_request_write_hosts") or []
                if host_pattern in hosts or host_pattern in write_hosts:
                    hits.append(row["id"])
            return hits

    def list_network_events(
        self,
        *,
        limit: int = 50,
        tool: str | None = None,
        host: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """返回带 details.network 的 tool.completed / tool.failed 事件，时间降序。

        Python 侧过滤，避免 sqlite json_extract 版本兼容。
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT e.id, e.edict_id, e.memorial_id, e.event_type,
                          e.payload_json, e.created_at, ed.title as edict_title
                   FROM events e
                   LEFT JOIN edicts ed ON ed.id = e.edict_id
                   WHERE e.event_type IN ('tool.completed', 'tool.failed')
                   ORDER BY e.created_at DESC
                   LIMIT ?""",
                (max(limit * 5, 200),),  # 预取多一些，留余量给 Python 过滤
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                continue
            details = payload.get("details") or {}
            network = details.get("network") if isinstance(details, dict) else None
            if not isinstance(network, dict):
                continue

            row_tool = network.get("tool") or payload.get("tool")
            row_is_error = bool(payload.get("is_error"))

            # 过滤
            if tool and row_tool != tool:
                continue
            if host and network.get("host") != host:
                continue
            if status == "ok" and row_is_error:
                continue
            if status == "error" and not row_is_error:
                continue

            out.append({
                "event_id": r["id"],
                "created_at": r["created_at"],
                "edict_id": r["edict_id"],
                "edict_title": r["edict_title"],
                "tool": row_tool,
                "host": network.get("host"),
                "method": network.get("method"),
                "http_status": network.get("http_status"),
                "bytes_out": network.get("bytes_out"),
                "credential_name": network.get("credential_name"),
                "cached": bool(network.get("cached", False)),
                "is_error": row_is_error,
                "reason": payload.get("result_preview") if row_is_error else None,
                "provider": network.get("provider"),            # web_search
                "result_count": network.get("result_count"),    # web_search
                "truncated": bool(network.get("truncated", False)),  # api_request
            })
            if len(out) >= limit:
                break
        return out

    # --- tool switches ---------------------------------------------------

    def list_disabled_tools(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_name FROM tool_switches WHERE enabled = 0"
            ).fetchall()
            return {r["tool_name"] for r in rows}

    def set_tool_enabled(self, tool_name: str, enabled: bool) -> None:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO tool_switches (tool_name, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tool_name) DO UPDATE SET
                     enabled = excluded.enabled,
                     updated_at = excluded.updated_at""",
                (tool_name, 1 if enabled else 0, now),
            )

    # --- mcp server overrides --------------------------------------------

    def list_mcp_overrides(self) -> list[dict]:
        """读取所有 mcp_server_overrides 行。

        nullable 字段语义：
          * 若 YAML 中存在同名 server：NULL = 沿用 YAML，非 NULL = 覆写
          * 若 YAML 中无同名 server：DB 必须填够 transport + 主字段，merge 时晋级为完整 server
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT name, enabled, env_json,
                          tools_include_json, tools_exclude_json,
                          transport, command, args_json,
                          url, headers_json,
                          default_tier, timeout, connect_timeout,
                          tool_overrides_json
                     FROM mcp_server_overrides"""
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "name": r["name"],
                "enabled": None if r["enabled"] is None else bool(r["enabled"]),
                "env": json.loads(r["env_json"]) if r["env_json"] else None,
                "tools_include": (
                    json.loads(r["tools_include_json"])
                    if r["tools_include_json"] else None
                ),
                "tools_exclude": (
                    json.loads(r["tools_exclude_json"])
                    if r["tools_exclude_json"] else None
                ),
                "transport": r["transport"],
                "command": r["command"],
                "args": json.loads(r["args_json"]) if r["args_json"] else None,
                "url": r["url"],
                "headers": (
                    json.loads(r["headers_json"]) if r["headers_json"] else None
                ),
                "default_tier": r["default_tier"],
                "timeout": r["timeout"],
                "connect_timeout": r["connect_timeout"],
                "tool_overrides": (
                    json.loads(r["tool_overrides_json"])
                    if r["tool_overrides_json"] else None
                ),
            })
        return out

    def upsert_mcp_override(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        env: dict[str, str] | None = None,
        tools_include: list[str] | None = None,
        tools_exclude: list[str] | None = None,
        transport: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        default_tier: int | None = None,
        timeout: int | None = None,
        connect_timeout: int | None = None,
        tool_overrides: dict[str, int] | None = None,
    ) -> None:
        """upsert 一行 server 配置；None 字段写入 NULL（= 沿用 YAML 或不指定）。"""
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                # COALESCE 让 NULL 入参表示「不动该字段」，保留旧值。
                # 这避免 PATCH 单字段时把其他列清成 NULL。
                # 想真的清字段 → 走 DELETE override 删整行后重建。
                """INSERT INTO mcp_server_overrides
                   (name, enabled, env_json, tools_include_json, tools_exclude_json,
                    transport, command, args_json, url, headers_json,
                    default_tier, timeout, connect_timeout, tool_overrides_json,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     enabled = COALESCE(excluded.enabled, mcp_server_overrides.enabled),
                     env_json = COALESCE(excluded.env_json, mcp_server_overrides.env_json),
                     tools_include_json = COALESCE(excluded.tools_include_json, mcp_server_overrides.tools_include_json),
                     tools_exclude_json = COALESCE(excluded.tools_exclude_json, mcp_server_overrides.tools_exclude_json),
                     transport = COALESCE(excluded.transport, mcp_server_overrides.transport),
                     command = COALESCE(excluded.command, mcp_server_overrides.command),
                     args_json = COALESCE(excluded.args_json, mcp_server_overrides.args_json),
                     url = COALESCE(excluded.url, mcp_server_overrides.url),
                     headers_json = COALESCE(excluded.headers_json, mcp_server_overrides.headers_json),
                     default_tier = COALESCE(excluded.default_tier, mcp_server_overrides.default_tier),
                     timeout = COALESCE(excluded.timeout, mcp_server_overrides.timeout),
                     connect_timeout = COALESCE(excluded.connect_timeout, mcp_server_overrides.connect_timeout),
                     tool_overrides_json = COALESCE(excluded.tool_overrides_json, mcp_server_overrides.tool_overrides_json),
                     updated_at = excluded.updated_at""",
                (
                    name,
                    None if enabled is None else (1 if enabled else 0),
                    json.dumps(env) if env is not None else None,
                    json.dumps(tools_include) if tools_include is not None else None,
                    json.dumps(tools_exclude) if tools_exclude is not None else None,
                    transport,
                    command,
                    json.dumps(args) if args is not None else None,
                    url,
                    json.dumps(headers) if headers is not None else None,
                    default_tier,
                    timeout,
                    connect_timeout,
                    json.dumps(tool_overrides) if tool_overrides is not None else None,
                    now,
                ),
            )

    def delete_mcp_override(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM mcp_server_overrides WHERE name = ?", (name,)
            )

    # --- engine preferences ---------------------------------------------

    def get_engine_preferences(self) -> dict:
        """返回 {fetch_chain, search_provider, fallback_mode,
        scrapling_dynamic_enabled, scrapling_stealthy_enabled};
        无记录返回全空（不覆盖 profile），开关默认 False。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetch_chain, search_provider, fallback_mode, "
                "scrapling_dynamic_enabled, scrapling_stealthy_enabled "
                "FROM engine_preferences WHERE id='default'"
            ).fetchone()
        if row is None:
            return {
                "fetch_chain": [],
                "search_provider": None,
                "fallback_mode": None,
                "scrapling_dynamic_enabled": False,
                "scrapling_stealthy_enabled": False,
            }
        chain = json.loads(row["fetch_chain"] or "[]")
        return {
            "fetch_chain": chain if isinstance(chain, list) else [],
            "search_provider": row["search_provider"],
            "fallback_mode": row["fallback_mode"],
            "scrapling_dynamic_enabled": bool(row["scrapling_dynamic_enabled"]),
            "scrapling_stealthy_enabled": bool(row["scrapling_stealthy_enabled"]),
        }

    def set_engine_preferences(
        self,
        *,
        fetch_chain: list[str],
        search_provider: str | None,
        fallback_mode: str | None,
        scrapling_dynamic_enabled: bool = False,
        scrapling_stealthy_enabled: bool = False,
    ) -> None:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO engine_preferences
                   (id, fetch_chain, search_provider, fallback_mode,
                    scrapling_dynamic_enabled, scrapling_stealthy_enabled, updated_at)
                   VALUES ('default', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     fetch_chain = excluded.fetch_chain,
                     search_provider = excluded.search_provider,
                     fallback_mode = excluded.fallback_mode,
                     scrapling_dynamic_enabled = excluded.scrapling_dynamic_enabled,
                     scrapling_stealthy_enabled = excluded.scrapling_stealthy_enabled,
                     updated_at = excluded.updated_at""",
                (
                    json.dumps(fetch_chain), search_provider, fallback_mode,
                    1 if scrapling_dynamic_enabled else 0,
                    1 if scrapling_stealthy_enabled else 0,
                    now,
                ),
            )

    # --- outer loop iterations ------------------------------------------

    def save_outer_loop_iteration(self, record: dict) -> None:
        """写入一条 outer loop iteration（dict 形式以避免循环 import）。"""
        with self._lock, self._conn:
            self._conn.execute("""
                INSERT INTO outer_loop_iterations
                (id, edict_id, iteration, level, actor_output, checks_result,
                 critic_result, cost_cny, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edict_id, iteration) DO NOTHING
            """, (
                record["id"], record["edict_id"], record["iteration"],
                record["level"], record["actor_output"],
                record["checks_result"], record["critic_result"],
                record["cost_cny"], record["started_at"], record["finished_at"],
            ))

    def get_outer_loop_iterations(self, edict_id: str) -> list[dict]:
        """按 iteration 升序返回所有迭代记录。"""
        rows = self._conn.execute("""
            SELECT id, edict_id, iteration, level, actor_output, checks_result,
                   critic_result, cost_cny, started_at, finished_at, archived_at
            FROM outer_loop_iterations
            WHERE edict_id = ?
            ORDER BY iteration ASC
        """, (edict_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_iterations_to_archive(self, before: str) -> list[str]:
        """返回 finished_at < before 且未归档的 iteration id 列表。"""
        rows = self._conn.execute("""
            SELECT id FROM outer_loop_iterations
            WHERE finished_at < ? AND archived_at IS NULL
        """, (before,)).fetchall()
        return [r["id"] for r in rows]

    def archive_iteration(self, iteration_id: str, archived_at: str) -> None:
        """归档：actor_output 置 NULL，archived_at 写时间戳。"""
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE outer_loop_iterations
                SET actor_output = NULL, archived_at = ?
                WHERE id = ?
            """, (archived_at, iteration_id))

    # --- outer loop checkpoints ------------------------------------------

    def save_outer_loop_checkpoint(self, edict_id: str, data_json: str, saved_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                INSERT INTO outer_loop_checkpoints (edict_id, data_json, saved_at)
                VALUES (?, ?, ?)
                ON CONFLICT(edict_id) DO UPDATE SET data_json=excluded.data_json, saved_at=excluded.saved_at
            """, (edict_id, data_json, saved_at))

    def get_outer_loop_checkpoint(self, edict_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT data_json FROM outer_loop_checkpoints WHERE edict_id = ?",
            (edict_id,),
        ).fetchone()
        return row["data_json"] if row else None

    def clear_outer_loop_checkpoint(self, edict_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM outer_loop_checkpoints WHERE edict_id = ?", (edict_id,),
            )

    # --- Supervision report (long task 终态总评) ---

    def save_supervision_report(self, record: dict) -> None:
        """写一行监督报告（PK = (memorial_id, persona_id)）。

        record 必带 memorial_id；旧调用方未传时会落 KeyError，强制升级到新 schema。
        """
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO supervision_reports
                   (edict_id, memorial_id, persona_id, persona_name, final_status,
                    iterations_count, total_cost_cny, report_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["edict_id"], record["memorial_id"],
                    record["persona_id"], record["persona_name"],
                    record["final_status"], record["iterations_count"],
                    record["total_cost_cny"], record["report_json"],
                    record["created_at"],
                ),
            )

    def get_supervision_report(self, edict_id: str) -> dict | None:
        """单监督官兼容入口；返同 edict 最新一行。"""
        row = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE edict_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (edict_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_supervision_reports(self, edict_id: str) -> list[dict]:
        """同 edict 全部报告，按 created_at DESC + persona_id 排序。"""
        rows = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE edict_id = ? "
            "ORDER BY created_at DESC, persona_id ASC",
            (edict_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_supervision_reports_by_memorial(self, memorial_id: str) -> list[dict]:
        """按 memorial 维度返回报告（每条奏折独立的监督报告）。"""
        rows = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE memorial_id = ? "
            "ORDER BY persona_id ASC",
            (memorial_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Feishu session anchor ---

    def get_feishu_anchor(
        self, chat_id: str, instance_id: str = "feishu-default"
    ) -> str | None:
        row = self._conn.execute(
            "SELECT current_edict_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    def set_feishu_anchor(
        self, chat_id: str, edict_id: str, instance_id: str = "feishu-default"
    ) -> None:
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT INTO feishu_session_anchor (instance_id, chat_id, current_edict_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, chat_id) DO UPDATE SET "
            "    current_edict_id = excluded.current_edict_id, "
            "    updated_at = excluded.updated_at",
            (instance_id, chat_id, edict_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def delete_feishu_anchor(
        self, chat_id: str, instance_id: str = "feishu-default"
    ) -> None:
        """`/exit` 用：清除该 chat 的 anchor，回到助手模式。"""
        self._conn.execute(
            "DELETE FROM feishu_session_anchor WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        )
        self._conn.commit()

    def has_sent_upgrade_notice(self, chat_id: str, version_tag: str) -> bool:
        """幂等检查：是否已对此 chat 发过该版本的升级通告。"""
        row = self._conn.execute(
            "SELECT 1 FROM feishu_pending_cards "
            "WHERE approval_id = ? AND kind = ?",
            (chat_id, f"upgrade_notice_{version_tag}"),
        ).fetchone()
        return row is not None

    def mark_upgrade_notice_sent(self, chat_id: str, version_tag: str) -> None:
        """标记已发送升级通告（用于幂等）。"""
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT OR IGNORE INTO feishu_pending_cards "
            "(approval_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, chat_id, "", f"upgrade_notice_{version_tag}",
             datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def list_active_anchor_chats(
        self, instance_id: str = "feishu-default"
    ) -> list[str]:
        """列出所有有活跃 anchor 的 chat（用于升级通告下发）。"""
        rows = self._conn.execute(
            "SELECT chat_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND current_edict_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Feishu typing reaction（替代 v1 的 "🤔 思考中" 卡片）---
    #
    # 沿用旧表 feishu_thinking_messages：`message_id` 列存 reaction_id，
    # `source_message_id` 列存用户原消息 id（reaction API 必需）。

    def save_feishu_thinking(
        self, *, memorial_id: str, chat_id: str, reaction_id: str,
        source_message_id: str,
    ) -> None:
        """登记一条 typing reaction，等 execution 完成时 remove。"""
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT OR REPLACE INTO feishu_thinking_messages "
            "(memorial_id, chat_id, message_id, source_message_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memorial_id, chat_id, reaction_id, source_message_id,
             datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_feishu_thinking(self, memorial_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, source_message_id "
            "FROM feishu_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM feishu_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        )
        self._conn.commit()
        return {
            "chat_id": row[0],
            "reaction_id": row[1],
            "source_message_id": row[2],
        }

    def list_chats_anchored_to(
        self, edict_id: str, instance_id: str = "feishu-default"
    ) -> list[str]:
        """反查：哪些飞书 chat 的 anchor 当前指向该 edict。

        用于飞书 outbound 在 edict.metadata.chat_id 缺失（web 创建敕令）时
        定位回执目标 —— 精准送回到 /select 切过来的那个 chat。
        """
        rows = self._conn.execute(
            "SELECT chat_id FROM feishu_session_anchor "
            "WHERE instance_id = ? AND current_edict_id = ?",
            (instance_id, edict_id),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Feishu dedup ---

    def is_feishu_message_seen(
        self, message_id: str, instance_id: str = "feishu-default"
    ) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM feishu_seen_messages WHERE message_id = ? AND instance_id = ?",
            (message_id, instance_id),
        ).fetchone()
        return row is not None

    def mark_feishu_message_seen(
        self, message_id: str, max_entries: int = 2048,
        instance_id: str = "feishu-default",
    ) -> None:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO feishu_seen_messages (message_id, instance_id, seen_at) "
            "VALUES (?, ?, ?)",
            (message_id, instance_id, now),
        )
        self._conn.execute(
            "DELETE FROM feishu_seen_messages WHERE instance_id = ? AND message_id IN ("
            "  SELECT message_id FROM feishu_seen_messages WHERE instance_id = ? "
            "  ORDER BY seen_at ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM feishu_seen_messages WHERE instance_id = ?) - ?))",
            (instance_id, instance_id, instance_id, max_entries),
        )
        self._conn.commit()

    # --- Feishu pending cards (Step 5 用) ---

    def save_feishu_pending_card(
        self, approval_id: str, chat_id: str, message_id: str, kind: str,
        instance_id: str = "feishu-default",
    ) -> None:
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT OR REPLACE INTO feishu_pending_cards "
            "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, instance_id, chat_id, message_id, kind,
             datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_feishu_pending_card(self, approval_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM feishu_pending_cards WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM feishu_pending_cards WHERE approval_id = ?", (approval_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    # --- Telegram session anchor（与飞书并列）---

    def get_telegram_anchor(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> str | None:
        row = self._conn.execute(
            "SELECT current_edict_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    def set_telegram_anchor(
        self, chat_id: str, edict_id: str, instance_id: str = "telegram-default"
    ) -> None:
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT INTO telegram_session_anchor (instance_id, chat_id, current_edict_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(instance_id, chat_id) DO UPDATE SET "
            "    current_edict_id = excluded.current_edict_id, "
            "    updated_at = excluded.updated_at",
            (instance_id, chat_id, edict_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def delete_telegram_anchor(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> None:
        self._conn.execute(
            "DELETE FROM telegram_session_anchor WHERE instance_id = ? AND chat_id = ?",
            (instance_id, chat_id),
        )
        self._conn.commit()

    def list_telegram_active_anchor_chats(
        self, instance_id: str = "telegram-default"
    ) -> list[str]:
        rows = self._conn.execute(
            "SELECT chat_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND current_edict_id IS NOT NULL",
            (instance_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def list_telegram_chats_anchored_to(
        self, edict_id: str, instance_id: str = "telegram-default"
    ) -> list[str]:
        """反查：哪些 telegram chat 的 anchor 指向该 edict（出站定位回执目标）。"""
        rows = self._conn.execute(
            "SELECT chat_id FROM telegram_session_anchor "
            "WHERE instance_id = ? AND current_edict_id = ?",
            (instance_id, edict_id),
        ).fetchall()
        return [row[0] for row in rows]

    # --- Telegram dedup ---

    def is_telegram_update_seen(
        self, update_id: str, instance_id: str = "telegram-default"
    ) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM telegram_seen_messages WHERE update_id = ? AND instance_id = ?",
            (update_id, instance_id),
        ).fetchone()
        return row is not None

    def mark_telegram_update_seen(
        self, update_id: str, max_entries: int = 2048,
        instance_id: str = "telegram-default",
    ) -> None:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO telegram_seen_messages (update_id, instance_id, seen_at) "
            "VALUES (?, ?, ?)",
            (update_id, instance_id, now),
        )
        self._conn.execute(
            "DELETE FROM telegram_seen_messages WHERE instance_id = ? AND update_id IN ("
            "  SELECT update_id FROM telegram_seen_messages WHERE instance_id = ? "
            "  ORDER BY seen_at ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM telegram_seen_messages WHERE instance_id = ?) - ?))",
            (instance_id, instance_id, instance_id, max_entries),
        )
        self._conn.commit()

    # --- Telegram thinking 占位消息（替代飞书 typing reaction）---

    def save_telegram_thinking(
        self, *, memorial_id: str, chat_id: str, message_id: str,
    ) -> None:
        """登记一条 ⏳ 占位消息，等 execution 完成时 delete。"""
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT OR REPLACE INTO telegram_thinking_messages "
            "(memorial_id, chat_id, message_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (memorial_id, chat_id, message_id, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_telegram_thinking(self, memorial_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id FROM telegram_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM telegram_thinking_messages WHERE memorial_id = ?",
            (memorial_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1]}

    # --- Telegram pending buttons（审批 inline keyboard 反查）---

    def save_telegram_pending_button(
        self, *, approval_id: str, chat_id: str, message_id: str, kind: str,
        instance_id: str = "telegram-default",
    ) -> None:
        from datetime import UTC, datetime
        self._conn.execute(
            "INSERT OR REPLACE INTO telegram_pending_buttons "
            "(approval_id, instance_id, chat_id, message_id, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, instance_id, chat_id, message_id, kind,
             datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pop_telegram_pending_button(self, approval_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM telegram_pending_buttons "
            "WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "DELETE FROM telegram_pending_buttons WHERE approval_id = ?", (approval_id,),
        )
        self._conn.commit()
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    def get_telegram_pending_button(self, approval_id: str) -> dict | None:
        """只读查询（不删除）：callback 处理时先看 pending 是否还在。"""
        row = self._conn.execute(
            "SELECT chat_id, message_id, kind FROM telegram_pending_buttons "
            "WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        return {"chat_id": row[0], "message_id": row[1], "kind": row[2]}

    def list_telegram_pending_for_chat(
        self, chat_id: str, instance_id: str = "telegram-default"
    ) -> list[str]:
        """该 chat 下尚未响应的待审批 memorial_id（approval 文本命令用）。"""
        rows = self._conn.execute(
            "SELECT approval_id FROM telegram_pending_buttons "
            "WHERE instance_id = ? AND chat_id = ? AND kind = 'tool.approval_required' "
            "ORDER BY created_at ASC",
            (instance_id, chat_id),
        ).fetchall()
        return [r[0] for r in rows]

    # --- Channel configs (通政司) ---

    def get_channel_config(self, channel_type: str) -> dict | None:
        """返回非敏感配置 dict + secret 是否已配（不返明文）。None 表示未配置。"""
        row = self._conn.execute(
            "SELECT config_json, encrypted_secret, updated_at "
            "FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()
        if not row:
            return None
        import json as _json
        cfg = _json.loads(row[0])
        cfg["_has_secret"] = row[1] is not None
        cfg["_updated_at"] = row[2]
        return cfg

    def save_channel_config(
        self,
        channel_type: str,
        config: dict,
        secret_plaintext: str | None = None,
    ) -> None:
        """保存配置；secret_plaintext=None 时不动 encrypted_secret，空串清空。"""
        import json as _json
        from datetime import UTC, datetime

        from tianshu.secrets.vault import get_vault

        # 排除内部字段
        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        config_json_str = _json.dumps(clean_config, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        encrypted: bytes | None = None
        update_secret = False
        if secret_plaintext is not None:
            update_secret = True
            if secret_plaintext == "":
                encrypted = None  # 清空
            else:
                vault = get_vault()
                if vault is None:
                    raise RuntimeError(
                        "TIANSHU_SECRET_MASTER_KEY 未设置，无法保存敏感凭证。"
                        "请先配置主密钥后重启服务。"
                    )
                encrypted = vault.encrypt(secret_plaintext)

        existing = self._conn.execute(
            "SELECT 1 FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()

        if existing:
            if update_secret:
                self._conn.execute(
                    "UPDATE channel_configs SET config_json=?, encrypted_secret=?, updated_at=? "
                    "WHERE channel_type=?",
                    (config_json_str, encrypted, now, channel_type),
                )
            else:
                self._conn.execute(
                    "UPDATE channel_configs SET config_json=?, updated_at=? "
                    "WHERE channel_type=?",
                    (config_json_str, now, channel_type),
                )
        else:
            self._conn.execute(
                "INSERT INTO channel_configs (channel_type, config_json, encrypted_secret, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (channel_type, config_json_str, encrypted, now),
            )
        self._conn.commit()

    def load_channel_runtime_config(self, channel_type: str) -> dict | None:
        """返回**含明文 secret** 的运行时配置；启动加载/reload 用，不暴露给 API。"""
        row = self._conn.execute(
            "SELECT config_json, encrypted_secret FROM channel_configs WHERE channel_type = ?",
            (channel_type,),
        ).fetchone()
        if not row:
            return None
        import json as _json

        from tianshu.secrets.vault import get_vault
        cfg = _json.loads(row[0])
        # 解密后的 secret 放到 channel 对应的字段：飞书=app_secret，telegram=bot_token
        secret_key = "bot_token" if channel_type == "telegram" else "app_secret"
        if row[1]:
            vault = get_vault()
            if vault is None:
                return None  # 配了 secret 但 vault 缺失 → 视为不可用
            try:
                cfg[secret_key] = vault.decrypt(row[1])
            except ValueError:
                return None
        else:
            cfg[secret_key] = ""
        return cfg

    # --- Channel instances（多 bot 实例）---

    def list_channel_instances(self, channel_type: str | None = None) -> list[dict]:
        """列实例（不含明文 secret）。每行展开 config + instance_id / channel_type /
        label / enabled(bool) / _has_secret / updated_at。"""
        import json as _json
        sql = (
            "SELECT instance_id, channel_type, label, enabled, config_json, "
            "encrypted_secret, updated_at FROM channel_instances"
        )
        params: tuple = ()
        if channel_type is not None:
            sql += " WHERE channel_type = ?"
            params = (channel_type,)
        sql += " ORDER BY channel_type, instance_id"
        rows = self._conn.execute(sql, params).fetchall()
        result: list[dict] = []
        for row in rows:
            cfg = _json.loads(row[4])
            cfg["instance_id"] = row[0]
            cfg["channel_type"] = row[1]
            cfg["label"] = row[2]
            cfg["enabled"] = bool(row[3])
            cfg["_has_secret"] = row[5] is not None
            cfg["updated_at"] = row[6]
            result.append(cfg)
        return result

    def get_channel_instance(self, instance_id: str) -> dict | None:
        """单条实例（不含明文 secret）。None 表示不存在。"""
        import json as _json
        row = self._conn.execute(
            "SELECT instance_id, channel_type, label, enabled, config_json, "
            "encrypted_secret, updated_at FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if not row:
            return None
        cfg = _json.loads(row[4])
        cfg["instance_id"] = row[0]
        cfg["channel_type"] = row[1]
        cfg["label"] = row[2]
        cfg["enabled"] = bool(row[3])
        cfg["_has_secret"] = row[5] is not None
        cfg["updated_at"] = row[6]
        return cfg

    def save_channel_instance(
        self,
        *,
        instance_id: str,
        channel_type: str,
        label: str,
        enabled: bool,
        config: dict,
        secret_plaintext: str | None = None,
    ) -> None:
        """保存实例。secret_plaintext=None 时不动 encrypted_secret，空串清空，
        非空则 vault.encrypt（vault 缺失 raise RuntimeError）。config 去掉 _ 开头的 key。"""
        import json as _json
        from datetime import UTC, datetime

        from tianshu.secrets.vault import get_vault

        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        config_json_str = _json.dumps(clean_config, ensure_ascii=False)
        now = datetime.now(UTC).isoformat()

        encrypted: bytes | None = None
        update_secret = False
        if secret_plaintext is not None:
            update_secret = True
            if secret_plaintext == "":
                encrypted = None  # 清空
            else:
                vault = get_vault()
                if vault is None:
                    raise RuntimeError(
                        "TIANSHU_SECRET_MASTER_KEY 未设置，无法保存敏感凭证。"
                        "请先配置主密钥后重启服务。"
                    )
                encrypted = vault.encrypt(secret_plaintext)

        existing = self._conn.execute(
            "SELECT 1 FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()

        if existing:
            if update_secret:
                self._conn.execute(
                    "UPDATE channel_instances SET channel_type=?, label=?, enabled=?, "
                    "config_json=?, encrypted_secret=?, updated_at=? WHERE instance_id=?",
                    (channel_type, label, int(enabled), config_json_str, encrypted,
                     now, instance_id),
                )
            else:
                self._conn.execute(
                    "UPDATE channel_instances SET channel_type=?, label=?, enabled=?, "
                    "config_json=?, updated_at=? WHERE instance_id=?",
                    (channel_type, label, int(enabled), config_json_str, now,
                     instance_id),
                )
        else:
            self._conn.execute(
                "INSERT INTO channel_instances "
                "(instance_id, channel_type, label, enabled, config_json, "
                "encrypted_secret, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (instance_id, channel_type, label, int(enabled), config_json_str,
                 encrypted, now),
            )
        self._conn.commit()

    def set_channel_instance_enabled(self, instance_id: str, enabled: bool) -> None:
        from datetime import UTC, datetime
        self._conn.execute(
            "UPDATE channel_instances SET enabled=?, updated_at=? WHERE instance_id=?",
            (int(enabled), datetime.now(UTC).isoformat(), instance_id),
        )
        self._conn.commit()

    def delete_channel_instance(self, instance_id: str) -> None:
        self._conn.execute(
            "DELETE FROM channel_instances WHERE instance_id = ?", (instance_id,),
        )
        self._conn.commit()

    def load_channel_instance_runtime(self, instance_id: str) -> dict | None:
        """返回**含明文 secret** 的运行时配置（启动/reload 用）。

        返回 dict 展开 config + instance_id / channel_type / label / enabled，
        且 secret 解密放到 bot_token(telegram) / app_secret(feishu)。
        vault 缺失或解密失败时：若该实例确实存了 secret 则返回 None（视为不可用）；
        无 secret 则 secret 字段填空串。
        """
        import json as _json

        from tianshu.secrets.vault import get_vault
        row = self._conn.execute(
            "SELECT channel_type, label, enabled, config_json, encrypted_secret "
            "FROM channel_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if not row:
            return None
        channel_type = row[0]
        cfg = _json.loads(row[3])
        cfg["instance_id"] = instance_id
        cfg["channel_type"] = channel_type
        cfg["label"] = row[1]
        cfg["enabled"] = bool(row[2])
        secret_key = "bot_token" if channel_type == "telegram" else "app_secret"
        if row[4]:
            vault = get_vault()
            if vault is None:
                return None  # 配了 secret 但 vault 缺失 → 视为不可用
            try:
                cfg[secret_key] = vault.decrypt(row[4])
            except ValueError:
                return None
        else:
            cfg[secret_key] = ""
        return cfg
