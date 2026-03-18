"""Configuration management via Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TianshuSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIANSHU_", env_file=".env")

    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_max_retries: int = 3
    llm_temperature: float = 0.7
    llm_top_p: float = 1.0
    llm_max_tokens: int = 4096
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    db_path: str = ".tianshu/tianshu.db"
    host: str = "0.0.0.0"
    port: int = 8000
    workspace_dir: str = "."
    skills_char_budget: int = 30000
    static_dir: str = "/app/static"
