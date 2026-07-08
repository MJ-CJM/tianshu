"""Configuration management via Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TianshuSettings(BaseSettings):
    # extra=ignore：允许通过 os.getenv 读取的 TIANSHU_* 运行期密钥（secret_master_key /
    # firecrawl_api_key / tavily_api_key / jina_api_key）不被 pydantic 拒绝启动
    model_config = SettingsConfigDict(
        env_prefix="TIANSHU_",
        env_file=".env",
        extra="ignore",
    )

    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_max_retries: int = 3
    llm_temperature: float = 0.7
    llm_top_p: float = 1.0
    llm_max_tokens: int = 4096
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    db_path: str = "~/.tianshu/tianshu.db"
    host: str = "0.0.0.0"
    port: int = 8000
    workspace_dir: str = "."
    skills_char_budget: int = 30000
    static_dir: str = "/app/static"
    memory_dir: str = "~/.tianshu/memory"
    runtime_personas_dir: str = "~/.tianshu/personas"
    log_dir: str = "~/.tianshu/logs"
    log_level: str = "INFO"
    # Phase 3: concurrency
    max_global_concurrency: int = 8
    # Phase 2: notification channels
    feishu_webhook: str = ""
    # Feishu Bot —— inbound + outbound (与 hermes 同名同义)
    feishu_app_id: str = ""  # 空 → 不启用机器人（向后兼容）
    feishu_app_secret: str = ""
    feishu_domain: str = "feishu"  # feishu | lark
    feishu_connection_mode: str = "websocket"  # websocket | webhook
    feishu_allowed_users: str = ""  # 逗号分隔 open_id
    feishu_home_channel: str = ""  # cron 结果 / 无源审批兜底 chat_id
    feishu_encrypt_key: str = ""  # webhook 模式签名密钥
    feishu_verification_token: str = ""  # webhook 模式 token 校验
    feishu_bot_open_id: str = ""  # 群 @ 检测
    feishu_bot_name: str = ""  # 群 @ 检测兜底
    feishu_webhook_path: str = "/feishu/webhook"
    feishu_ws_reconnect_interval: int = 120
    feishu_text_batch_delay: float = 0.6
    feishu_dedup_cache_size: int = 2048
    feishu_assistant_persona_id: str = "tongzheng"
    feishu_intent_llm_enabled: bool = True
    feishu_disable_assistant_mode: bool = False
    # Telegram Bot —— inbound + outbound（与飞书并列；空 token → 不启用，向后兼容）
    telegram_bot_token: str = ""  # 空 → 不启用机器人
    telegram_connection_mode: str = "polling"  # polling | webhook
    telegram_allowed_users: str = ""  # 逗号分隔 user_id（int）
    telegram_home_channel: str = ""  # cron 结果 / 无源审批兜底 chat_id（群为负数）
    telegram_webhook_path: str = "/telegram/webhook"
    telegram_webhook_secret: str = ""  # webhook 模式 X-Telegram-Bot-Api-Secret-Token
    telegram_poll_timeout: int = 30  # getUpdates 长轮询超时（秒）
    telegram_text_batch_delay: float = 0.6  # 文本批处理静默期
    telegram_dedup_cache_size: int = 2048
    telegram_assistant_persona_id: str = "tongzheng"
    telegram_disable_assistant_mode: bool = False
    telegram_enable_edict_submission: bool = False
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    parallel_universe_enabled: bool = False
    code_variant_enabled: bool = False
    eval_mode: bool = False  # 沙箱评估模式：外发副作用（通知/webhook）应被 stub
    # 沙箱评估专用 LLM 凭证(空 = 沿用宿主凭证)。untrusted 变体进程在评估期
    # 能拿到 LLM key,配置低额度专用 key 可把泄漏面压到额度上限。
    eval_llm_api_key: str = ""
    eval_llm_api_base: str = ""
    eval_llm_model: str = ""
    # --- 迭代 3「深防御」---
    # 出厂预算护栏(放手四保险第④条):每日全局预算上限(CNY),0=不设护栏。
    # 首次启动若库中无 global 预算,自动落此默认值(超限熔断 + 通知)。
    daily_budget_guardrail_cny: float = 20.0
    # opt-in 遥测(ADR-0003):默认关;设 TIANSHU_TELEMETRY=on 才启用;
    # 仅上报版本 + 启动事件,代码可审计,一行 env 永久关。
    telemetry: str = "off"  # off | on
    telemetry_endpoint: str = ""  # 空 → 只记本地遥测日志,不外发
    # OTel GenAI 埋点:默认关;设 OTLP endpoint 才导出。
    otel_endpoint: str = ""  # 例 http://localhost:4318 (Phoenix/OTLP)
    # MCP 治理·准入清单(D15):逗号分隔的 server 名白名单。空=不强制(允许全部
    # enabled server,启动时明示未设护栏);非空=只启动清单内 server,其余拒并告警。
    mcp_server_allowlist: str = ""
    # --- 迭代 5:通政司通知三级制(D2)---
    # 免打扰时段(保守默认 23:00–08:00):normal 通知在此时段攒起来、醒后补推;
    # urgent 穿透免打扰立即外发;low 不即时外发、入 digest。start==end 关闭免打扰。
    notify_quiet_hours_start: int = 23
    notify_quiet_hours_end: int = 8
