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
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
