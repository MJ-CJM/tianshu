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
    feishu_app_id: str = ""                       # 空 → 不启用机器人（向后兼容）
    feishu_app_secret: str = ""
    feishu_domain: str = "feishu"                 # feishu | lark
    feishu_connection_mode: str = "websocket"     # websocket | webhook
    feishu_allowed_users: str = ""                # 逗号分隔 open_id
    feishu_home_channel: str = ""                 # cron 结果 / 无源审批兜底 chat_id
    feishu_encrypt_key: str = ""                  # webhook 模式签名密钥
    feishu_verification_token: str = ""           # webhook 模式 token 校验
    feishu_bot_open_id: str = ""                  # 群 @ 检测
    feishu_bot_name: str = ""                     # 群 @ 检测兜底
    feishu_webhook_path: str = "/feishu/webhook"
    feishu_ws_reconnect_interval: int = 120
    feishu_text_batch_delay: float = 0.6
    feishu_dedup_cache_size: int = 2048
    feishu_assistant_persona_id: str = "tongzheng"
    feishu_intent_llm_enabled: bool = True
    feishu_disable_assistant_mode: bool = False
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
