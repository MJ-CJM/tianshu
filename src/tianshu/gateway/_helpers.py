"""Gateway 路由跨簇共享的 helper 函数。"""

from __future__ import annotations

from tianshu.models import Edict, Memorial, TaskStatus

# --- Helper to build history from previous memorials ---


def _build_history(edict: Edict, memorials: list[Memorial]) -> list[dict]:
    """构建给 executor 的多轮对话历史。

    DeepSeek reasoner / 新版 thinking-mode 模型在 multi-turn follow_up 时
    要求 history 中**每一条** assistant 消息都带 reasoning_content，否则
    返回 400 invalid_request_error（"must be passed back to the API"）。

    后向兼容：reasoning_content 字段是 2026-04-30 才加的，更早的 memorial 没存。
    对那些 assistant 整条跳过（仅保留 user 消息维持上下文），避免 DeepSeek 报错。
    """
    history: list[dict] = []
    for m in memorials:
        if m.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            continue
        instruction = m.instruction or edict.goal
        history.append({"role": "user", "content": instruction})
        if not m.result:
            continue
        if not m.reasoning_content:
            # 老 memorial：assistant 整条跳过，避免触发 thinking-mode 校验
            continue
        history.append(
            {
                "role": "assistant",
                "content": m.result,
                "reasoning_content": m.reasoning_content,
            }
        )
    return history
