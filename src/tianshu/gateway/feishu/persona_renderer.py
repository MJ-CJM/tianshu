"""根据绑定 persona 渲染飞书回信文案。零 LLM 调用，纯模板。

AgentPersona 实际字段：id / name / department / soul_path / role_path /
                        llm_config_name / tools_allowed / ... （无 emoji / title 字段）
本模块用 department → emoji 映射表和 name 作为称呼。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.persona.loader import AgentPersona

logger = logging.getLogger(__name__)


# Department 到 emoji 的映射（与百官阁视觉一致）
_DEPT_EMOJI: dict[str, str] = {
    "tongzheng": "📜",   # 通政司：对外通信
    "binbu": "⚔️",       # 兵部：奉敕调用工具
    "hubu": "💰",        # 户部：成本与预算
    "libu": "🎓",        # 礼部：技能/培训
    "ducha": "⚖️",       # 都察院：审计
    "cabinet": "🏛️",     # 内阁：决策
    "neishi": "📖",      # 内侍/侍读
}

DEFAULT_EMOJI = "🏛️"
DEFAULT_NAME = "侍读"


@dataclass(frozen=True)
class RenderedPersona:
    """简化的 persona 视图，仅含飞书回信渲染需要的字段。"""
    name: str
    emoji: str
    department: str


class PersonaRenderer:
    """根据绑定 persona 渲染回信文案。"""

    def __init__(self, persona: "AgentPersona | None") -> None:
        self._persona = self._resolve(persona)

    @staticmethod
    def _resolve(persona: "AgentPersona | None") -> RenderedPersona:
        if persona is None:
            logger.warning(
                "[feishu/persona] no persona bound, falling back to default '%s'",
                DEFAULT_NAME,
            )
            return RenderedPersona(name=DEFAULT_NAME, emoji=DEFAULT_EMOJI, department="default")
        emoji = _DEPT_EMOJI.get(persona.department, DEFAULT_EMOJI)
        return RenderedPersona(
            name=persona.name or DEFAULT_NAME,
            emoji=emoji,
            department=persona.department or "default",
        )

    @property
    def name(self) -> str:
        return self._persona.name

    @property
    def emoji(self) -> str:
        return self._persona.emoji

    # --- 模式标记 ---

    @staticmethod
    def assistant_tag() -> str:
        return "💼 助手"

    @staticmethod
    def edict_tag(edict_id: str) -> str:
        return f"📋 敕令 #{edict_id[:8]}"

    # --- 回信文案 ---

    def welcome(self) -> str:
        return f"{self.emoji} {self.name} 在此候命。输入 /menu 打开菜单，或 /help 查看命令。"

    def help_assistant(self) -> str:
        return (
            f"{self.assistant_tag()} 可用命令：\n"
            "- `/new <目标>` 新建敕令\n"
            "- `/list [filter]` 查看敕令列表（filter: open/completed/all）\n"
            "- `/select <id>` 切换到指定敕令\n"
            "- `/budget` 成本概览\n"
            "- `/menu` 主菜单\n"
            "- `/help` 显示帮助"
        )

    def help_edict(self, edict_id: str) -> str:
        return (
            f"{self.edict_tag(edict_id)} 可用命令：\n"
            "- 纯文本 = 续接当前敕令\n"
            "- `/status` 查看状态\n"
            "- `/cancel` 取消敕令\n"
            "- `/exit` 退出回到助手模式\n"
            "- `/new <目标>` 自动退出 + 新建\n"
            "- `/list /budget /menu` 查询（不切换）"
        )

    def edict_created_reply(self, edict_id: str, title: str) -> str:
        return f"{self.assistant_tag()} → ✅ 新敕令 #{edict_id[:8]}「{title}」已颁，进入敕令模式"

    def edict_received_reply(self, edict_id: str) -> str:
        return f"{self.edict_tag(edict_id)} 已收到"

    def edict_selected_reply(self, edict_id: str, title: str) -> str:
        return f"{self.edict_tag(edict_id)} 已切换（标题：{title}）"

    def edict_exit_reply(self) -> str:
        return f"{self.assistant_tag()} 已退出敕令模式"

    def edict_cancel_reply(self, edict_id: str) -> str:
        return f"{self.edict_tag(edict_id)} 已取消"

    def unknown_command_reply(self, mode_tag: str, cmd: str) -> str:
        return f"{mode_tag} 未识此令「{cmd}」，输入 /help 查看可用命令"

    def assistant_silent_reply(self) -> str:
        return f"{self.assistant_tag()} {self.name} 待命中。请用 /help 查看命令，或 /menu 打开菜单。"

    def llm_intent_hint(self, intent: str) -> str:
        return f"💡 我理解你想：{intent}"


__all__ = ["PersonaRenderer", "RenderedPersona", "DEFAULT_NAME", "DEFAULT_EMOJI"]
