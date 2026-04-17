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

    # Keywords per department for smart matching
    _DEPARTMENT_KEYWORDS: dict[str, list[str]] = {
        "bingbu": ["execute", "run", "deploy", "build", "implement", "create"],
        "ducha": ["audit", "review", "check", "inspect", "verify", "validate"],
        "hubu": ["cost", "budget", "finance", "expense", "token"],
        "wenyuan": ["memory", "knowledge", "search", "recall", "document"],
        "tongzheng": ["notify", "alert", "message", "report", "communicate"],
        "neige": ["plan", "strategy", "coordinate", "decide", "synthesize"],
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
