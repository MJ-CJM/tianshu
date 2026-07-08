"""Storage 数据库迁移 —— 从 facade._migrate 抽出，SQL 内容与拆分前完全一致。"""

import sqlite3


def run_migrations(conn: sqlite3.Connection) -> None:
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
        "CREATE INDEX IF NOT EXISTS idx_supervision_edict ON supervision_reports(edict_id)",
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
        # 2026-07-02: Multica 借鉴 #1 —— 孤儿任务回收心跳字段
        "ALTER TABLE memorials ADD COLUMN last_heartbeat_at TEXT",
        # 2026-07-02: Multica 借鉴 #2 —— 调度触发台账（cron/interval + 系统 job）
        """CREATE TABLE IF NOT EXISTS schedule_run (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                edict_id TEXT,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            )""",
        "CREATE INDEX IF NOT EXISTS idx_schedule_run_source ON schedule_run(source)",
        # 2026-07-04: 位面竞争力——配对基线(冠军同集沙箱评估分)落台账
        "ALTER TABLE variant_eval_runs ADD COLUMN baseline_json TEXT",
        # 2026-07-08: 迭代 2「证明」—— 失败原因分类学(multica 14 类 + 天枢平台侧 3 类)
        "ALTER TABLE memorials ADD COLUMN failure_reason TEXT",
        "CREATE INDEX IF NOT EXISTS idx_memorials_failure_reason "
        "ON memorials(failure_reason) WHERE failure_reason IS NOT NULL",
        # 2026-07-08: 迭代 2「证明」—— 平台级回归评测(评测集 + 评测运行台账)
        """CREATE TABLE IF NOT EXISTS eval_sets (
                name TEXT PRIMARY KEY,
                goals_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'sampled',
                created_at TEXT NOT NULL
            )""",
        """CREATE TABLE IF NOT EXISTS eval_runs (
                id TEXT PRIMARY KEY,
                eval_set_name TEXT,
                eval_set_fingerprint TEXT NOT NULL,
                target TEXT NOT NULL,
                fitness_json TEXT NOT NULL,
                stats_json TEXT NOT NULL,
                goal_results_json TEXT,
                n INTEGER NOT NULL,
                truncated INTEGER NOT NULL DEFAULT 0,
                delta_vs_prev REAL,
                created_at TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS idx_eval_runs_fingerprint ON eval_runs(eval_set_fingerprint)",
        # 2026-07-08: 迭代 3「深防御」—— 分级急停单行状态(锦衣卫)
        """CREATE TABLE IF NOT EXISTS estop_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                kill_all INTEGER NOT NULL DEFAULT 0,
                network_kill INTEGER NOT NULL DEFAULT 0,
                frozen_tools_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT,
                reason TEXT
            )""",
        # 2026-07-08: 迭代 3 —— 预算周期滚动(daily/weekly 预算跨期自动清零)
        "ALTER TABLE cost_budgets ADD COLUMN period_start TEXT",
        # 2026-07-08: 迭代 3.5「客卿」—— 影子快照台账(放手四保险③)
        """CREATE TABLE IF NOT EXISTS shadow_snapshots (
                id TEXT PRIMARY KEY,
                edict_id TEXT NOT NULL,
                memorial_id TEXT,
                sha TEXT NOT NULL,
                label TEXT NOT NULL,
                work_tree TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS idx_shadow_edict ON shadow_snapshots(edict_id)",
        # 2026-07-08: 迭代 4「记忆 2.0」—— 时序知识图谱(三元组 + 有效期 + as_of 查询)
        """CREATE TABLE IF NOT EXISTS kg_triples (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'court',
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'agent',
                created_at TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS idx_kg_sp ON kg_triples(scope, subject, predicate)",
        "CREATE INDEX IF NOT EXISTS idx_kg_valid ON kg_triples(valid_to)",
        # 2026-07-08: 迭代 4「记忆 2.0」—— 后台史官蒸馏台账(防重复蒸馏)
        """CREATE TABLE IF NOT EXISTS historian_log (
                memorial_id TEXT PRIMARY KEY,
                distilled_at TEXT NOT NULL,
                insight_written INTEGER NOT NULL DEFAULT 0
            )""",
        # 2026-07-08: 迭代 5「执行 2.0」—— 免打扰待发通知(醒后补推,不丢)
        """CREATE TABLE IF NOT EXISTS pending_notifications (
                id TEXT PRIMARY KEY,
                edict_id TEXT,
                memorial_id TEXT,
                message_json TEXT NOT NULL,
                channels_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        # 2026-07-08: 迭代 5「执行 2.0」—— steer 中途注入(长任务在线纠偏,下一轮吸收)
        """CREATE TABLE IF NOT EXISTS pending_steers (
                id TEXT PRIMARY KEY,
                edict_id TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        "CREATE INDEX IF NOT EXISTS idx_steers_edict ON pending_steers(edict_id)",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
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
            conn.execute(f"ALTER TABLE persona_metrics ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e) and "no such column" not in str(e):
                raise
    conn.commit()

    # 2026-07-08 迭代 2:历史 failed 行的 failure_reason 自动回填(multica MUL-1949
    # backfill 思路)。只补 NULL 行故幂等;与写路径共用同一分类函数保证在库口径统一。
    # 分类器升级后的全量重分类走 `tianshu evals backfill --re-classify`。
    from tianshu.models.failure import resolve_failure_reason

    rows = conn.execute(
        "SELECT id, error FROM memorials WHERE status = 'failed' AND failure_reason IS NULL"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE memorials SET failure_reason = ? WHERE id = ?",
            (resolve_failure_reason("failed", row["error"], None), row["id"]),
        )
    conn.commit()
