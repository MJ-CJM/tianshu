"""会话档执行的协议面与规范事件 envelope。

与单发 KeqingAdapter Protocol **并列、不杂交**(critic 裁决):单发档(claude-code/codex)
是"拼一条 argv → 读 stdout 行流 → 事后 parse"的批处理;会话档是 RPC 长连接——stdin 写
命令(带 id)、stdout 收事件流、结算只认 agent_settled、follow_up 回灌验收整改。两者生命
周期(退出码终态 vs settled+EOF 优雅收尾)与拦截位(无 vs 会中 write_stdin/terminate)本质
不同,故独立协议,单发档零改动陪跑。

CanonicalAgentEvent 是异构下游(pi/claude_code/codex)工具与生命周期事件的归一 envelope
(方案 B2):kind 开放集(未知沉降 unknown 不炸)、dialect 封闭、text/tool_name 投影式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# --- CanonicalAgentEvent.kind 开放枚举(未知沉降 unknown,向前兼容 pi 演进) ---
KIND_RUN_START = "run_start"
KIND_RUN_END = "run_end"  # 一次 agent run 结束,may retry(will_retry)
KIND_RUN_SETTLED = "run_settled"  # 彻底 settle:编排器据此推进/验收的收口锚点
KIND_TURN_END = "turn_end"
KIND_MESSAGE = "message"  # 一条 assistant 消息终态(带 text/usage)
KIND_TOOL_START = "tool_start"
KIND_TOOL_END = "tool_end"
KIND_UI_REQUEST = "ui_request"  # 扩展反向通道请求(P4 接批红)
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentCapabilities:
    """客卿能力声明:编排器据此启用软增强并对不支持项明示降级(grok ToolCapabilities 范式)。

    permission_shaping/hooks 在 P4 tianshu-guard 就位后才为真(pi 进程内扩展拦截);
    P2 裸跑档如实声明为 none/none,其余会话能力(续用/插话/计费)本阶段即生效。
    """

    permission_shaping: Literal["full", "partial", "none"] = "none"
    hooks: Literal["in_process", "command_shim", "http", "none"] = "none"
    stop_gate: bool = False  # 收工时刻可强制验收(settled 后跑 checks)
    session_resume: bool = False  # follow_up 同会话回灌整改(免重启)
    interject: bool = False  # 运行中插话(steer/follow_up)
    usage_reporting: Literal["full", "partial", "none"] = "none"


@dataclass(frozen=True)
class CanonicalAgentEvent:
    """归一事件 envelope。dialect 标注来源方言;raw_type 保留原始事件名供审计。"""

    kind: str
    dialect: str
    raw_type: str | None = None
    will_retry: bool = False
    tool_name: str | None = None
    is_error: bool = False
    text: str | None = None


@dataclass
class SessionRunStats:
    """会话终账(pi get_session_stats 归一):跨全会话聚合的 token/成本。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


@runtime_checkable
class KeqingSessionAdapter(Protocol):
    """会话档适配器契约。pi(PiSessionAdapter)是首个实现。

    单发 KeqingAdapter(build_argv/is_canonical_argv/parse_stream)不实现本协议;
    两协议唯一共享接缝是 is_canonical_argv → issue_keqing_command_grant 的 argv 校验
    (由单发 PiAdapter 兼容接受 RPC form 提供,见 pi_adapter.py)。
    """

    name: str
    dialect: str
    capabilities: AgentCapabilities
    auth_env_vars: tuple[str, ...]

    def build_session_argv(
        self, *, session_dir: str | None = None, model: str | None = None, resume: bool = False
    ) -> list[str]: ...

    def encode_command(
        self,
        cmd_type: str,
        *,
        cmd_id: str | None = None,
        message: str | None = None,
        **fields: object,
    ) -> bytes: ...

    def parse_event(self, frame: dict) -> CanonicalAgentEvent: ...

    def is_settled(self, event: CanonicalAgentEvent) -> bool: ...

    def is_response(self, frame: dict) -> bool: ...

    def extract_stats(self, data: dict) -> SessionRunStats: ...
