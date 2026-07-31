"""Official selector — assign personas to tasks by type."""

from __future__ import annotations

from tianshu.persona.loader import PersonaLoader
from tianshu.persona.model import AgentPersona


class OfficialSelector:
    """Maps task types to official (persona) IDs dynamically based on loaded personas."""

    # Preferred department for each task type (used to find the best persona)
    TASK_DEPARTMENT_PREFERENCE: dict[str, str] = {
        "plan": "neige",
        "execute": "bingbu",
        "audit": "ducha",
        "notify": "tongzheng",
        "memory": "wenyuan",
        "cost": "hubu",
    }

    # Keywords per department for smart matching（中英双语：goal 多为中文，
    # 纯英文关键词对中文旨意恒 score=0，兜底会退化成"第一个非内阁官员"）
    _DEPARTMENT_KEYWORDS: dict[str, list[str]] = {
        "bingbu": [
            "execute",
            "run",
            "deploy",
            "build",
            "implement",
            "create",
            "执行",
            "运行",
            "部署",
            "构建",
            "实现",
            "创建",
            "开发",
            "修复",
            "编写",
            "脚本",
        ],
        "ducha": [
            "audit",
            "review",
            "check",
            "inspect",
            "verify",
            "validate",
            "审计",
            "审查",
            "核查",
            "检查",
            "校验",
            "验证",
            "复核",
        ],
        "hubu": [
            "cost",
            "budget",
            "finance",
            "expense",
            "token",
            "成本",
            "预算",
            "费用",
            "开销",
            "账单",
        ],
        "wenyuan": [
            "memory",
            "knowledge",
            "search",
            "recall",
            "document",
            "记忆",
            "知识",
            "检索",
            "查询",
            "回忆",
            "文档",
            "资料",
            "历史",
            "翻译",
            "总结",
            "介绍",
            "解释",
            "是什么",
            "为什么",
            "几位",
            "多少",
        ],
        "tongzheng": [
            "notify",
            "alert",
            "message",
            "report",
            "communicate",
            "通知",
            "提醒",
            "消息",
            "播报",
            "告知",
        ],
        "neige": [
            "plan",
            "strategy",
            "coordinate",
            "decide",
            "synthesize",
            "规划",
            "策略",
            "统筹",
            "协调",
            "决策",
        ],
    }

    def __init__(self, loader: PersonaLoader) -> None:
        self._loader = loader

    def _find_by_department(self, department: str) -> AgentPersona | None:
        """Find the first persona belonging to a department."""
        for p in self._loader._personas.values():
            if p.department == department:
                return p
        return None

    def _fallback_persona(self) -> AgentPersona | None:
        """Return a reasonable fallback — first non-neige persona, or any."""
        for p in self._loader._personas.values():
            if p.department != "neige":
                return p
        # If only neige exists, return it
        for p in self._loader._personas.values():
            return p
        return None

    def select(self, task_type: str) -> AgentPersona | None:
        """Select the appropriate persona for a task type."""
        dept = self.TASK_DEPARTMENT_PREFERENCE.get(task_type)
        if dept:
            persona = self._find_by_department(dept)
            if persona:
                return persona
        return self._fallback_persona()

    def select_for_task(self, description: str) -> AgentPersona | None:
        """Smart-match a persona based on task description keywords."""
        desc_lower = description.lower()
        best_dept: str | None = None
        best_score = 0

        for dept, keywords in self._DEPARTMENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in desc_lower)
            if score > best_score:
                best_score = score
                best_dept = dept

        if best_dept:
            persona = self._find_by_department(best_dept)
            if persona:
                return persona
        return self._fallback_persona()

    def get_default_map(self) -> dict[str, dict]:
        """Build the default task→persona map from actually loaded personas.

        For task types whose preferred department has no persona,
        include a fallback entry so the UI can show which tasks are missing
        dedicated officials.
        """
        fallback = self._fallback_persona()
        result = {}
        for task_type, dept in self.TASK_DEPARTMENT_PREFERENCE.items():
            persona = self._find_by_department(dept)
            if persona:
                result[task_type] = {
                    "persona_id": persona.id,
                    "name": persona.name,
                    "department": persona.department,
                    "preferred_department": dept,
                    "is_fallback": False,
                }
            elif fallback:
                result[task_type] = {
                    "persona_id": fallback.id,
                    "name": fallback.name,
                    "department": fallback.department,
                    "preferred_department": dept,
                    "is_fallback": True,
                }
        return result

    def get_keyword_map(self) -> dict[str, dict]:
        """Build the keyword map from actually loaded personas."""
        result = {}
        for dept, keywords in self._DEPARTMENT_KEYWORDS.items():
            persona = self._find_by_department(dept)
            if persona:
                result[persona.id] = {
                    "name": persona.name,
                    "department": persona.department,
                    "keywords": keywords,
                }
        return result

    def list_all(self) -> list[AgentPersona]:
        """Return all loaded personas from in-memory cache."""
        return list(self._loader._personas.values())
