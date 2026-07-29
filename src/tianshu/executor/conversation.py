"""跟进批示的多轮对话历史——回放本敕令先前奏折的 user/assistant 轮次。

原先 feishu bridge 与 gateway API 各持一份 `_build_history`，managed-run
重构后两处都断线成死代码（follow-up 全部丢上下文）。现收敛为执行器内的
单一实现：所有入口（web follow-up / 飞书 / Telegram / API）统一获得连续对话。

回放边界（2026-07-29 审计修订）：
- 只回放 COMPLETED 的根奏折：FAILED/CANCELLED 会造成"悬空 user 指令"与
  重试链的同指令重复投喂；DAG 子节点奏折（dag_node_id 非空）是机器生成的
  任务分解，不是用户说过的话。
- 轮数与字符双预算（从最近轮往前取），防止长对话/周期敕令把上下文打满。
- assistant 只回放纯文本 content，不带 reasoning_content——DeepSeek 官方
  约束输入 messages 含该字段会 400，严格 openai-compat 端点亦可能拒收
  未知字段；跨 provider 混用（统一注册表后是常态）时纯文本最稳。
"""

from __future__ import annotations

from tianshu.models import Edict, Memorial, TaskStatus

# 回放预算：最多 20 轮 / 约 60K 字符（≈ 20-30K token），超出从最早轮丢弃。
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_CHARS = 60_000


def build_conversation_history(
    edict: Edict,
    memorials: list[Memorial],
    *,
    exclude_memorial_id: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    """把先前成功的根奏折回放为 user/assistant 轮次（入参须按时间升序）。"""
    turns: list[tuple[dict, dict | None]] = []
    for m in memorials:
        if (
            m.id == exclude_memorial_id
            or m.status is not TaskStatus.COMPLETED
            or getattr(m, "dag_node_id", None) is not None
        ):
            continue
        user = {"role": "user", "content": m.instruction or edict.goal}
        assistant = {"role": "assistant", "content": m.result} if m.result else None
        turns.append((user, assistant))

    selected = turns[-max_turns:] if max_turns > 0 else turns
    # 字符预算从最近轮往前累计；至少保留最近一轮（超预算也保）。
    history: list[dict] = []
    used = 0
    for user, assistant in reversed(selected):
        size = len(user["content"] or "") + (len(assistant["content"] or "") if assistant else 0)
        if history and used + size > max_chars:
            break
        pair = [user] + ([assistant] if assistant else [])
        history = pair + history
        used += size
    return history
