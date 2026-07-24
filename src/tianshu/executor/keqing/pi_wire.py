"""pi headless JSON 模式的 wire 帧模型(单一真相)。

钉 pi 版本:@earendil-works/pi-coding-agent(MIT)。pi `--mode json` 每行输出一个
AgentSessionEvent JSON;首行是 session header(type=session)。天枢单发档只消费终态
相关帧,宽容解析(字段缺失降级,不崩)——不同 pi 版本字段名可能微调,评估失败安全。

参照 pi docs/json.md 与 packages/ai/src/types.ts 的 AssistantMessage.usage 结构。
会话档(RPC 模式)的命令帧见 P2 的 keqing/session.py。
"""

from __future__ import annotations

from pydantic import BaseModel

# 钉死的 pi 版本(@earendil-works/pi-coding-agent, bin 名 pi)。契约测试对此版本回归;
# pi 升级时先跑契约套件验证 wire 兼容,绿了再更新此常量。会话档 spawn 时可探测
# `pi --version` 与此比对(P2),不符则告警——让 pi 演进对天枢可见而非静默漂移。
PINNED_PI_VERSION = "0.81.1"

# --- AgentSessionEvent / AgentEvent 的 type 常量(pi docs/json.md) ---
EVT_SESSION = "session"  # 首行 header,非事件
EVT_AGENT_START = "agent_start"
EVT_AGENT_END = "agent_end"  # {messages: AgentMessage[]}
EVT_TURN_END = "turn_end"  # {message, toolResults}
EVT_MESSAGE_END = "message_end"  # {message: AgentMessage}
EVT_TOOL_EXEC_START = "tool_execution_start"  # {toolCallId, toolName, args}
EVT_TOOL_EXEC_END = "tool_execution_end"  # {toolCallId, toolName, result, isError}

# pi 的 headless 单发命令基线(build_argv 用):`pi --mode json --no-session <prompt>`
PI_MODE = "json"

# 天枢已验证兼容的 pi session 格式版本(json 输出首行 header 的 version 字段)。
# 演进策略(回应"pi 持续演进天枢能否支持"):
#   - 非破坏性演进(pi 加事件类型/字段/provider):parse_stream 宽容解析自动吸收,天枢零改动;
#   - 破坏性演进(改字段名/命令结构):仅需改本模块 + pi_adapter 两个薄文件,治理核心不受影响;
#   - 协议版本漂移:parse_stream 检测到 session header version != 此值时告警(不静默出错),
#     提示先跑 pi 契约测试验证兼容,绿了再更新此常量与钉死的 pi 版本。
VERIFIED_SESSION_VERSION = 3


class PiCost(BaseModel):
    """AssistantMessage.usage.cost:各维度美元成本。"""

    input: float = 0.0
    output: float = 0.0
    cacheRead: float = 0.0
    cacheWrite: float = 0.0
    total: float = 0.0


class PiUsage(BaseModel):
    """AssistantMessage.usage:token 计数 + 成本。pi 的 usage 是 per-message。"""

    input: int = 0
    output: int = 0
    cacheRead: int = 0
    cacheWrite: int = 0
    totalTokens: int = 0
    cost: PiCost = PiCost()
