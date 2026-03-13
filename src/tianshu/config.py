"""Configuration management via Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TianshuSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIANSHU_")

    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_max_retries: int = 3
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    db_path: str = ".tianshu/tianshu.db"
    host: str = "0.0.0.0"
    port: int = 8000
    workspace_dir: str = "."
    skills_char_budget: int = 30000
    static_dir: str = "/app/static"
