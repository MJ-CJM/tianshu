"""Official selector — assign personas to tasks by type."""

from __future__ import annotations

from tianshu.persona.loader import PersonaLoader
from tianshu.persona.model import AgentPersona


class OfficialSelector:
    """Maps task types to official (persona) IDs."""

    DEFAULT_MAP: dict[str, str] = {
        "plan": "neige",
        "execute": "bingbu",
        "audit": "ducha",
        "notify": "tongzheng",
        "memory": "wenyuan",
        "cost": "hubu",
    }

    # Keywords for smart matching in select_for_task
    _KEYWORD_MAP: dict[str, list[str]] = {
        "bingbu": ["execute", "run", "deploy", "build", "implement", "create"],
        "ducha": ["audit", "review", "check", "inspect", "verify", "validate"],
        "hubu": ["cost", "budget", "finance", "expense", "token"],
        "wenyuan": ["memory", "knowledge", "search", "recall", "document"],
        "tongzheng": ["notify", "alert", "message", "report", "communicate"],
        "neige": ["plan", "strategy", "coordinate", "decide", "synthesize"],
    }

    def __init__(self, loader: PersonaLoader) -> None:
        self._loader = loader

    def select(self, task_type: str) -> AgentPersona | None:
        """Select the appropriate persona for a task type."""
        persona_id = self.DEFAULT_MAP.get(task_type)
        if not persona_id:
            return None
        return self._loader.get(persona_id)

    def select_for_task(self, description: str) -> AgentPersona | None:
        """Smart-match a persona based on task description keywords."""
        desc_lower = description.lower()
        best_id: str | None = None
        best_score = 0

        for persona_id, keywords in self._KEYWORD_MAP.items():
            score = sum(1 for kw in keywords if kw in desc_lower)
            if score > best_score:
                best_score = score
                best_id = persona_id

        if best_id:
            return self._loader.get(best_id)
        # Default to bingbu (execution) for unmatched tasks
        return self._loader.get("bingbu")

    def list_all(self) -> list[AgentPersona]:
        """Return all loaded personas."""
        personas = self._loader.load_all()
        return list(personas.values())
