"""Configuration management via Pydantic Settings."""

import ipaddress
import math
import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ASSISTANT_PERSONA_ID = "qb"
"""渠道助手（通政司）默认 persona id。

必须是 personas 表中**在编官员**的 id，不能填部门名——部门目录
（resources/personas/<dept>/）只作部门级 prompt 模板，PersonaLoader 在有
storage 时只从 DB 装载（loader._seed_from_files 是 no-op），填部门名会让
loader.get() 返回 None，prompt_builder 只注入裸 _BASE_IDENTITY，助手静默
退化成"无人格"。历史上此处误用部门名 "tongzheng" 正是这个原因。

定义在 config（最低层）而非 persona.model：import-linter 分层契约禁止
config 向上依赖 persona。"""

_SHA256_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


class TianshuSettings(BaseSettings):
    # extra=ignore：允许通过 os.getenv 读取的 TIANSHU_* 运行期密钥（secret_master_key /
    # firecrawl_api_key / tavily_api_key / jina_api_key）不被 pydantic 拒绝启动
    model_config = SettingsConfigDict(
        env_prefix="TIANSHU_",
        env_file=".env",
        extra="ignore",
    )

    # demo 为精确 opt-in 的零网络确定性档位（TIANSHU_STARTUP_PROFILE=demo）；
    # live provider 失败永不回退 demo。
    startup_profile: Literal["live", "demo"] = "live"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_max_retries: int = 3
    llm_temperature: float = 0.7
    llm_top_p: float = 1.0
    llm_max_tokens: int = 4096
    # models.dev 目录价（USD）→ 天枢记账口径（CNY）的换算汇率；不做实时汇率。
    llm_usd_cny_rate: float = 7.2
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    db_path: str = "~/.tianshu/tianshu.db"
    artifact_dir: str = "~/.tianshu/artifacts"
    artifact_max_bytes: int = 100 * 1024 * 1024
    artifact_quota_bytes: int = 5 * 1024 * 1024 * 1024
    evolution_routing_secret: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    security_mode: Literal["trusted-local", "secure-remote"] = "trusted-local"
    public_base_url: str = ""
    allowed_hosts: str = ""
    allowed_origins: str = ""
    trusted_proxy_cidrs: str = ""
    auth_bootstrap_token_hash: str = ""
    auth_access_token_ttl_seconds: int = 900
    auth_refresh_token_ttl_seconds: int = 2592000
    # Only for containers whose host port is independently bound to loopback.
    # Direct/public use of this override would turn trusted-local into a remote trust boundary.
    trusted_local_container_boundary: bool = False
    # Exact Docker host gateway observed by the container; never a CIDR or broad private range.
    trusted_local_container_gateway: str = ""
    workspace_dir: str = "."
    workspace_staging_root: str = "~/.tianshu/workspaces"
    skills_char_budget: int = 30000
    static_dir: str = ""  # 空 = 使用打包 Web 资产；显式值（含 TIANSHU_STATIC_DIR）优先
    plugins_dir: str = "~/.tianshu/plugins"
    universe_repo_root: str = ""  # 空 = 开发模式回退仓库根推断；wheel 部署必须显式配置
    eval_repo_root: str = ""
    memory_dir: str = "~/.tianshu/memory"
    runtime_personas_dir: str = "~/.tianshu/personas"
    log_dir: str = "~/.tianshu/logs"
    log_level: str = "INFO"
    # Phase 3: concurrency
    max_global_concurrency: int = 8
    # Durable outbox worker. Per-row max_attempts remains durable row state;
    # ingress configuration of that value belongs to the unified-ingress slice.
    outbox_poll_interval_seconds: float = 0.25
    outbox_lease_seconds: int = 30
    durable_retry_base_seconds: float = 1.0
    durable_retry_max_seconds: float = 300.0
    outbox_shutdown_timeout_seconds: float = 5.0
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
    feishu_webhook_path: str = "/channels/feishu/webhook"
    feishu_ws_reconnect_interval: int = 120
    feishu_text_batch_delay: float = 0.6
    feishu_dedup_cache_size: int = 2048
    feishu_assistant_persona_id: str = DEFAULT_ASSISTANT_PERSONA_ID
    feishu_intent_llm_enabled: bool = True
    feishu_disable_assistant_mode: bool = False
    # Telegram Bot —— inbound + outbound（与飞书并列；空 token → 不启用，向后兼容）
    telegram_bot_token: str = ""  # 空 → 不启用机器人
    telegram_connection_mode: str = "polling"  # polling | webhook
    telegram_allowed_users: str = ""  # 逗号分隔 user_id（int）
    telegram_home_channel: str = ""  # cron 结果 / 无源审批兜底 chat_id（群为负数）
    telegram_webhook_path: str = "/channels/telegram/webhook"
    telegram_webhook_secret: str = ""  # webhook 模式 X-Telegram-Bot-Api-Secret-Token
    telegram_poll_timeout: int = 30  # getUpdates 长轮询超时（秒）
    telegram_text_batch_delay: float = 0.6  # 文本批处理静默期
    telegram_dedup_cache_size: int = 2048
    telegram_assistant_persona_id: str = DEFAULT_ASSISTANT_PERSONA_ID
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
    # LSP 诊断(迭代 5):默认关。开启后 edit_file 编辑 .py 落盘即跑 basedpyright,
    # 类型/语义诊断回灌 agent(需 `pip install 'tianshu-agent-os[lsp]'`；不可用返回关联 advisory)。
    lsp_enabled: bool = False

    @property
    def allowed_hosts_list(self) -> tuple[str, ...]:
        return _split_csv(self.allowed_hosts)

    @property
    def allowed_origins_list(self) -> tuple[str, ...]:
        return _split_csv(self.allowed_origins)

    @property
    def trusted_proxy_cidrs_list(self) -> tuple[str, ...]:
        return _split_csv(self.trusted_proxy_cidrs)

    @field_validator(
        "artifact_max_bytes",
        "artifact_quota_bytes",
        "outbox_poll_interval_seconds",
        "outbox_lease_seconds",
        "durable_retry_base_seconds",
        "durable_retry_max_seconds",
        "outbox_shutdown_timeout_seconds",
        mode="before",
    )
    @classmethod
    def reject_boolean_bounded_settings(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("bounded numeric settings cannot be boolean")
        return value

    @model_validator(mode="after")
    def validate_artifact_store(self) -> Self:
        if type(self.artifact_max_bytes) is not int or self.artifact_max_bytes <= 0:
            raise ValueError("artifact_max_bytes must be a positive integer")
        if (
            type(self.artifact_quota_bytes) is not int
            or self.artifact_quota_bytes < self.artifact_max_bytes
        ):
            raise ValueError("artifact_quota_bytes must be at least artifact_max_bytes")
        return self

    @model_validator(mode="after")
    def validate_outbox_worker(self) -> Self:
        timing_values = {
            "outbox_poll_interval_seconds": self.outbox_poll_interval_seconds,
            "durable_retry_base_seconds": self.durable_retry_base_seconds,
            "durable_retry_max_seconds": self.durable_retry_max_seconds,
            "outbox_shutdown_timeout_seconds": self.outbox_shutdown_timeout_seconds,
        }
        for name, value in timing_values.items():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if type(self.outbox_lease_seconds) is not int or self.outbox_lease_seconds <= 0:
            raise ValueError("outbox_lease_seconds must be a positive integer")
        if self.outbox_lease_seconds <= 2 * self.outbox_poll_interval_seconds:
            raise ValueError("outbox_lease_seconds must exceed two polling intervals")
        if self.durable_retry_max_seconds < self.durable_retry_base_seconds:
            raise ValueError("durable_retry_max_seconds must be at least the retry base")
        return self

    @model_validator(mode="after")
    def validate_security_mode(self) -> Self:
        if self.security_mode == "trusted-local":
            if self.trusted_local_container_boundary:
                if self.host not in {"0.0.0.0", "::"}:
                    raise ValueError(
                        "trusted-local container boundary requires a wildcard container bind"
                    )
                if not self._valid_container_gateway(self.trusted_local_container_gateway):
                    raise ValueError("trusted-local container gateway must be one exact private IP")
            elif self.trusted_local_container_gateway:
                raise ValueError("trusted-local container gateway requires the container boundary")
            elif not self._host_is_loopback(self.host):
                raise ValueError(
                    "trusted-local host must be loopback unless the container boundary is enabled"
                )
            return self

        if self.trusted_local_container_boundary:
            raise ValueError("secure-remote cannot use the trusted-local container boundary")
        if self.trusted_local_container_gateway:
            raise ValueError("secure-remote cannot use a trusted-local container gateway")

        required = {
            "public_base_url": self.public_base_url,
            "allowed_hosts": self.allowed_hosts,
            "allowed_origins": self.allowed_origins,
            "trusted_proxy_cidrs": self.trusted_proxy_cidrs,
            "auth_bootstrap_token_hash": self.auth_bootstrap_token_hash,
        }
        missing = sorted(name for name, value in required.items() if not value.strip())
        if missing:
            raise ValueError(f"secure-remote requires: {', '.join(missing)}")

        public_url = urlsplit(self.public_base_url)
        if public_url.scheme != "https" or not public_url.hostname:
            raise ValueError("secure-remote public_base_url must be an https URL")
        if public_url.username or public_url.password or public_url.query or public_url.fragment:
            raise ValueError(
                "secure-remote public_base_url cannot contain credentials/query/fragment"
            )

        hosts = self.allowed_hosts_list
        if any("*" in host or "://" in host for host in hosts):
            raise ValueError("secure-remote allowed_hosts must contain exact host names")

        origins = self.allowed_origins_list
        for origin in origins:
            parsed = urlsplit(origin)
            if origin == "*" or parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("secure-remote allowed_origins must contain exact https origins")
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError(
                    "secure-remote allowed_origins cannot contain paths/query/fragment"
                )

        for cidr in self.trusted_proxy_cidrs_list:
            network = ipaddress.ip_network(cidr, strict=False)
            if network.prefixlen == 0:
                raise ValueError(
                    "secure-remote trusted proxy CIDRs cannot cover the whole internet"
                )

        if _SHA256_HASH.fullmatch(self.auth_bootstrap_token_hash) is None:
            raise ValueError("auth_bootstrap_token_hash must be sha256:<64 lowercase hex chars>")
        if self.auth_access_token_ttl_seconds <= 0 or self.auth_refresh_token_ttl_seconds <= 0:
            raise ValueError("authentication token TTLs must be positive")
        return self

    @staticmethod
    def _host_is_loopback(host: str) -> bool:
        if host.strip().lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _valid_container_gateway(value: str) -> bool:
        try:
            gateway = ipaddress.ip_address(value)
        except ValueError:
            return False
        return any(gateway in network for network in _CONTAINER_PRIVATE_NETWORKS)
