"""Prompt builder — 8-layer system prompt injection for persona-aware agents.

Layer injection order:
  1: Base Identity
  2: COURT.md (shared court context)           — from personas/
  3: SOUL.md (persona identity)                — from personas/
  4: ROLE.md (persona role specifics)           — from personas/
  5: MEMORY.md (core long-term memory)          — from ~/.tianshu/memory/
  5.1: L1 Critical Facts (Memory Palace)        — from DrawerStore
  5.5: Recent Activity (last 2 days logs)       — from ~/.tianshu/memory/
  6: Court MEMORY.md (shared court memory)      — from ~/.tianshu/memory/
  7: Skills
  8: Task Context
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.memory.config import MemoryConfig
from tianshu.memory.markdown_backend import MarkdownMemoryBackend
from tianshu.models.edict import Edict
from tianshu.persona.model import AgentPersona
from tianshu.skills.loader import SkillsLoader

if TYPE_CHECKING:
    from tianshu.memory.drawer import MemoryBackend
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_BASE_IDENTITY = (
    "You are Tianshu, an AI execution assistant. "
    "Follow the user's instructions and use available tools to complete tasks. "
    "When done, summarize the result concisely. "
    "If you cannot complete the task, explain why."
)

_NETWORK_CAPABILITY_HINT = """# 外部网络能力

You may have the following network tools available (check the tool list for your current profile):

- `web_fetch(url)` — read a public webpage, get Markdown
- `web_search(query, max_results)` — web search
- `api_request(url, method, headers?, query?, json_body?)` — call any whitelisted external API
- `web_extract(url, schema, prompt?)` — structured extraction with Firecrawl

Safety rules when using `api_request`:
- **NEVER pass `Authorization`, `Cookie`, `X-Api-Key`, or similar auth headers in `headers`.** The system
  auto-injects credentials based on target host; user-supplied auth headers are rejected.
- You can only call hosts in the Edict's `api_request_hosts` whitelist — others return `host_not_whitelisted`.
- Write methods (POST/PUT/DELETE/PATCH) additionally require the host to be in `api_request_write_hosts`
  AND will trigger a human approval prompt.
- Rate limits apply (per-Edict, per-tool sliding window).
"""


class PromptBuilder:
    """Builds system prompts with 8-layer injection."""

    _SANITIZE_PATTERNS = (
        "```",
        "[INST]",
        "[/INST]",
        "<|system|>",
        "<|user|>",
        "<|assistant|>",
    )

    def __init__(
        self,
        personas_dir: Path,
        skills_loader: SkillsLoader,
        memory_dir: Path | None = None,
        metrics_store: object | None = None,
        drawer_store: MemoryBackend | None = None,
        memory_config: MemoryConfig | None = None,
        storage: Storage | None = None,
    ) -> None:
        self._personas_dir = personas_dir
        self._skills = skills_loader
        self._metrics_store = metrics_store
        self._drawer_store = drawer_store
        self._memory_config = memory_config or MemoryConfig()
        self._storage = storage  # 用于 Layer 2.5 身份卡查部门中文名（可选）

        if memory_dir is None:
            memory_dir = Path("~/.tianshu/memory").expanduser()
        self._memory_dir = Path(memory_dir).expanduser()

        self._md_backend = MarkdownMemoryBackend(
            memory_dir=self._memory_dir,
            personas_dir=self._personas_dir,
        )

        self._include_peer_profiles = True
        self._peer_profile_max_chars = 600

    async def build(
        self,
        edict: Edict,
        persona: AgentPersona | None = None,
        skills_char_budget: int = 30000,
    ) -> str:
        """Build system prompt with 8-layer injection order."""
        parts: list[str] = []

        # Layer 1: Base Identity
        parts.append(_BASE_IDENTITY)

        if persona:
            # Layer 2: COURT.md (shared court context) — from personas/
            court_path = self._personas_dir / "court" / "COURT.md"
            court_text = self._read_file(court_path)
            if court_text:
                parts.append(f"# Court Protocol\n\n{court_text}")

            # Layer 2.5: Identity Card — 权威身份卡，覆盖任何下文中可能的旧身份描述
            parts.append(self._build_identity_card(persona))

            # Layer 3: SOUL.md (persona identity) — from personas/
            soul_text = self._read_file(persona.soul_path)
            if soul_text:
                parts.append(f"# Persona Identity [{persona.id}]\n\n{soul_text}")
            else:
                # Fallback: generate minimal identity from persona metadata
                parts.append(
                    f"# Persona Identity [{persona.id}]\n\n"
                    f"You are {persona.name}, serving in the {persona.department} department. "
                    f"Your persona ID is {persona.id}. Respond in character."
                )
                logger.warning(
                    "SOUL.md not found for persona %s (%s), using fallback identity",
                    persona.id,
                    persona.soul_path,
                )

            # Layer 4: ROLE.md (persona role specifics) — from personas/
            role_text = self._read_file(persona.role_path)
            if role_text:
                parts.append(f"# Role Specification\n\n{role_text}")

            # Layer 5: MEMORY.md — from ~/.tianshu/memory/{persona}/
            memory_text = self._md_backend.read_core_memory(persona.id)
            if memory_text:
                parts.append(f"# Agent Memory\n\n{memory_text}")

            # Layer 5.1: L1 Critical Facts (Memory Palace)
            if self._drawer_store and self._memory_config.l1_enabled:
                l1 = await self._get_l1(persona.id)
                if l1:
                    parts.append(l1)

            # Layer 5.5: Recent Activity (last 2 days logs)
            recent_logs = self._md_backend.read_recent_logs(
                persona.id,
                days=2,
                char_budget=2000,
            )
            if recent_logs:
                parts.append(f"# Recent Activity\n\n{recent_logs}")

            # Layer 5.6: Department MEMORY.md — 部门内同侪共享池
            dept_mem_text = self._md_backend.read_core_memory(
                f"_dept/{persona.department}",
            )
            if dept_mem_text:
                dept_label = self._resolve_dept_name(persona.department)
                parts.append(f"# Department Memory ({dept_label})\n\n{dept_mem_text}")

            # Layer 6: Court MEMORY.md — from ~/.tianshu/memory/court/
            court_mem_text = self._md_backend.read_core_memory("court")
            if court_mem_text:
                parts.append(f"# Court Memory\n\n{court_mem_text}")

            # Layer 6.2: 起居注·主人偏好(迭代 4)——官员开工前见主人习惯,不等进化立即贴合
            profile_path = self._memory_dir / "court" / "USER_PROFILE.md"
            if profile_path.exists():
                profile_text = profile_path.read_text(encoding="utf-8").strip()
                if profile_text:
                    parts.append(f"# 主人偏好(起居注)\n\n{profile_text}")

            # Layer 6.5: Peer Profiles (同僚近况)
            peer_text = await self._build_peer_profiles(persona.id, edict)
            if peer_text:
                parts.append(peer_text)

        # Layer 7: Skills — index + always-on skills
        self._skills.set_char_budget(skills_char_budget)
        filter_names = persona.skills_allowed if persona and persona.skills_allowed else None

        # 7a: Inject skill index (name + description) for progressive loading
        index_text = self._skills.load_index(
            filter_names=filter_names,
            metrics_store=self._metrics_store,
        )
        if index_text:
            parts.append(index_text)

        # 7b: Inject full content for always=true skills
        always_text = self._skills.load_always(filter_names=filter_names)
        if always_text:
            parts.append(always_text)

        # Layer 7c: Network capability orientation (if any network tool registered)
        parts.append(_NETWORK_CAPABILITY_HINT)

        # Layer 8: Task Context
        parts.append(f"Current task ID: {edict.id}")

        return "\n\n".join(parts)

    async def build_layers(
        self,
        edict: Edict,
        persona: AgentPersona | None = None,
        skills_char_budget: int = 30000,
    ) -> dict:
        """Build system prompt and return per-layer breakdown."""
        layers: list[dict] = []

        # Layer 1: Base Identity
        layers.append(
            {
                "layer": 1,
                "name": "Base Identity",
                "source": "(builtin)",
                "chars": len(_BASE_IDENTITY),
                "tokens_est": len(_BASE_IDENTITY) // 4,
            }
        )

        if persona:
            # Layer 2: COURT.md
            court_path = self._personas_dir / "court" / "COURT.md"
            court_text = self._read_file(court_path)
            layers.append(
                {
                    "layer": 2,
                    "name": "COURT.md",
                    "source": str(court_path),
                    "chars": len(court_text),
                    "tokens_est": len(court_text) // 4,
                }
            )

            # Layer 2.5: Identity Card
            id_card = self._build_identity_card(persona)
            layers.append(
                {
                    "layer": 2.5,
                    "name": "Identity Card",
                    "source": "(persona name + department + title)",
                    "chars": len(id_card),
                    "tokens_est": len(id_card) // 4,
                }
            )

            # Layer 3: SOUL.md
            soul_text = self._read_file(persona.soul_path)
            layers.append(
                {
                    "layer": 3,
                    "name": "SOUL.md",
                    "source": str(persona.soul_path),
                    "chars": len(soul_text),
                    "tokens_est": len(soul_text) // 4,
                }
            )

            # Layer 4: ROLE.md
            role_text = self._read_file(persona.role_path)
            layers.append(
                {
                    "layer": 4,
                    "name": "ROLE.md",
                    "source": str(persona.role_path),
                    "chars": len(role_text),
                    "tokens_est": len(role_text) // 4,
                }
            )

            # Layer 5: MEMORY.md
            memory_text = self._md_backend.read_core_memory(persona.id)
            layers.append(
                {
                    "layer": 5,
                    "name": "MEMORY.md",
                    "source": f"~/.tianshu/memory/{persona.id}/MEMORY.md",
                    "chars": len(memory_text),
                    "tokens_est": len(memory_text) // 4,
                }
            )

            # Layer 5.1: L1 Critical Facts (Memory Palace)
            l1_text = ""
            if self._drawer_store and self._memory_config.l1_enabled:
                l1_text = await self._get_l1(persona.id)
            layers.append(
                {
                    "layer": 5.1,
                    "name": "L1 Critical Facts",
                    "source": "(Memory Palace)",
                    "chars": len(l1_text),
                    "tokens_est": len(l1_text) // 4,
                }
            )

            # Layer 5.5: Recent Activity
            recent_logs = self._md_backend.read_recent_logs(
                persona.id,
                days=2,
                char_budget=2000,
            )
            layers.append(
                {
                    "layer": 5.5,
                    "name": "Recent Activity",
                    "source": f"~/.tianshu/memory/{persona.id}/logs/",
                    "chars": len(recent_logs),
                    "tokens_est": len(recent_logs) // 4,
                }
            )

            # Layer 5.6: Department MEMORY.md
            dept_mem = self._md_backend.read_core_memory(
                f"_dept/{persona.department}",
            )
            layers.append(
                {
                    "layer": 5.6,
                    "name": "Department MEMORY.md",
                    "source": f"~/.tianshu/memory/_dept/{persona.department}/MEMORY.md",
                    "chars": len(dept_mem),
                    "tokens_est": len(dept_mem) // 4,
                }
            )

            # Layer 6: Court MEMORY.md
            court_mem = self._md_backend.read_core_memory("court")
            layers.append(
                {
                    "layer": 6,
                    "name": "Court MEMORY.md",
                    "source": "~/.tianshu/memory/court/MEMORY.md",
                    "chars": len(court_mem),
                    "tokens_est": len(court_mem) // 4,
                }
            )

            # Layer 6.5: Peer Profiles (同僚近况)
            peer_text = await self._build_peer_profiles(persona.id, edict)
            layers.append(
                {
                    "layer": 6.5,
                    "name": "Peer Profiles (同僚近况)",
                    "source": "~/.tianshu/personas/{peer}/PROFILE.md",
                    "chars": len(peer_text),
                    "tokens_est": len(peer_text) // 4,
                }
            )

        # Layer 7: Skills — index + always-on
        self._skills.set_char_budget(skills_char_budget)
        filter_names = persona.skills_allowed if persona and persona.skills_allowed else None
        index_text = self._skills.load_index(
            filter_names=filter_names,
            metrics_store=self._metrics_store,
        )
        always_text = self._skills.load_always(filter_names=filter_names)
        skills_total = len(index_text) + len(always_text)
        layers.append(
            {
                "layer": 7,
                "name": "Skills (index + always)",
                "source": "(skills loader)",
                "chars": skills_total,
                "tokens_est": skills_total // 4,
                "char_budget": skills_char_budget,
                "filtered_by": filter_names,
            }
        )

        # Layer 8: Task Context
        task_ctx = f"Current task ID: {edict.id}"
        layers.append(
            {
                "layer": 8,
                "name": "Task Context",
                "source": "(runtime)",
                "chars": len(task_ctx),
                "tokens_est": len(task_ctx) // 4,
            }
        )

        total_chars = sum(layer["chars"] for layer in layers)
        total_tokens = sum(layer["tokens_est"] for layer in layers)

        return {
            "persona_id": persona.id if persona else None,
            "total_chars": total_chars,
            "total_tokens_est": total_tokens,
            "layers": layers,
        }

    async def _build_peer_profiles(self, self_persona_id: str, edict) -> str:
        """Layer 6.5: inject other in-session personas' PROFILE excerpts."""
        if not self._include_peer_profiles:
            return ""
        peers = self._resolve_peers(edict, exclude=self_persona_id)
        if not peers:
            return ""
        entries: list[str] = []
        for pid in peers:
            entry = self._read_peer_profile(pid)
            if entry:
                entries.append(entry)
        if not entries:
            return ""
        return "## 同僚近况\n\n" + "\n\n".join(entries)

    def _resolve_peers(self, edict, exclude: str) -> list[str]:
        ids: set[str] = set()
        if getattr(edict, "assigned_persona_id", None):
            ids.add(edict.assigned_persona_id)
        if getattr(edict, "planner_persona_id", None):
            ids.add(edict.planner_persona_id)
        dag = getattr(edict, "dag_assignments", None) or []
        for a in dag:
            pid = a.get("assigned_official") if isinstance(a, dict) else None
            if pid:
                ids.add(pid)
        ids.discard(exclude)
        return sorted(ids)

    def _read_peer_profile(self, persona_id: str) -> str:
        from tianshu.persona.profile_schema import parse_profile

        path = self._memory_dir.parent / "personas" / persona_id / "PROFILE.md"
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("read PROFILE failed %s: %s", path, e)
            return ""
        fm, auto, _ = parse_profile(text)
        if not fm:
            return ""
        return self._extract_peer_summary(fm, auto)

    def _extract_peer_summary(self, frontmatter, auto_section: str) -> str:
        """Return ≤ peer_profile_max_chars summary, no degradation, sanitized."""
        specialties = self._slice_section(auto_section, "## 擅长领域")
        distribution = self._slice_section(auto_section, "## 近期任务分布")
        health = self._slice_section(auto_section, "## 健康度")
        # NEVER include "## 退化迹象"
        body = (
            f"### {frontmatter.persona_name} "
            f"(v{frontmatter.version}, {frontmatter.last_synthesized[:10]})\n"
            f"**擅长**:{self._oneline(specialties, 240)}\n"
            f"**近期**:{self._oneline(distribution, 160)}\n"
            f"**健康度**:{self._oneline(health, 120)}"
        )
        body = self._sanitize(body)
        if len(body) > self._peer_profile_max_chars:
            body = self._clip(body, self._peer_profile_max_chars)
        return body

    @staticmethod
    def _slice_section(text: str, header: str) -> str:
        if header not in text:
            return ""
        start = text.index(header) + len(header)
        rest = text[start:]
        # stop at next top-level heading
        for i, ch in enumerate(rest):
            if ch == "#" and rest[i : i + 3] == "## " and i != 0:
                return rest[:i].strip()
        return rest.strip()

    @staticmethod
    def _oneline(text: str, limit: int) -> str:
        t = " ".join(text.split())
        return t[:limit]

    @classmethod
    def _sanitize(cls, text: str) -> str:
        for pat in cls._SANITIZE_PATTERNS:
            text = text.replace(pat, "")
        return text

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        # strip table rows first, then trailing bullets
        lines = text.split("\n")
        while len(text) > limit and any(line.startswith("|") for line in lines):
            lines = [line for line in lines if not line.startswith("|")]
            text = "\n".join(lines)
        if len(text) > limit:
            text = text[: limit - 3].rstrip() + "..."
        return text

    async def _get_l1(self, persona_id: str) -> str:
        """Fetch L1 critical facts from drawer store (no-op if store missing)."""
        from tianshu.memory.layers import MemoryStack

        try:
            # 调用方均已用 `if self._drawer_store` 守卫，此处仅为 mypy 窄化
            assert self._drawer_store is not None
            stack = MemoryStack(store=self._drawer_store, config=self._memory_config)
            return await stack.get_l1(persona_id)
        except Exception:
            logger.debug("L1 injection skipped for %s", persona_id)
            return ""

    def _build_identity_card(self, persona: AgentPersona) -> str:
        """Layer 2.5：渲染权威身份卡，明确 name / department / title 并要求自报。

        无条件注入，优先级高于 SOUL.md。这是为了解决"新建官员（如王阳明）的 SOUL.md
        从部门模板拷贝过来后仍自称'内阁首辅'"的问题——身份卡里的强指令会让 LLM
        采信这里给出的真实身份。
        """
        dept_label = self._resolve_dept_name(persona.department)
        title = (persona.title or "").strip()
        title_clause = f"，担任**{title}**一职" if title else ""
        # "如何自报"句的拼写
        if title:
            self_intro = f"{persona.name}（{dept_label}·{title}）"
        else:
            self_intro = f"{persona.name}（{dept_label}）"
        return (
            "# 身份卡 (Identity Card)\n\n"
            f"- 姓名：**{persona.name}**\n"
            f"- 部门：**{dept_label}**（{persona.department}）\n"
            f"- 职务：**{title or '（未指派）'}**\n\n"
            f"你是 **{persona.name}**，隶属 **{dept_label}**{title_clause}。"
            f"当圣上或他人问起你的身份时，必须自报为「{self_intro}」。"
            "下文 SOUL/ROLE 中描述的是你的性格、行事风格与能力边界——它们服务于这个身份，不可改写或覆盖此身份。"
        )

    def _resolve_dept_name(self, department_id: str) -> str:
        """Resolve department's display name (中文名). Falls back to department_id."""
        storage = self._storage
        if storage is not None:
            try:
                dept = storage.get_department(department_id)
                if dept and dept.get("name"):
                    return str(dept["name"])
            except Exception:
                logger.debug("get_department failed for %s", department_id)
        return department_id

    @staticmethod
    def _read_file(path: Path) -> str:
        """Read a file, stripping frontmatter if present."""
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
            # Strip YAML frontmatter
            if text.startswith("---"):
                try:
                    end = text.index("---", 3)
                    text = text[end + 3 :].strip()
                except ValueError:
                    pass
            return text
        except Exception:
            logger.warning("Failed to read %s", path)
            return ""
