"""Persona role-template library — vendored agency-agents templates.

Templates live under ``templates/persona/{lang}/{category}/*.md`` (vendored by
``scripts/sync_persona_templates.py``). Each is a single markdown file with
YAML frontmatter (``name``/``description``/``emoji``/``color``) and a body split
into a personality section and a mission/role section.

``split_template`` maps one template file onto tianshu's two-file identity
model: SOUL.md (人格) + ROLE.md (职责), the files consumed by
``prompt_builder`` via ``persona.soul_path`` / ``persona.role_path``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

LANGS = ("zh", "en")

# Heading text (emoji/# stripped, lowercased) that marks the start of the
# mission/role part of the body — everything before it is personality (SOUL).
_MISSION_MARKERS = (
    "核心使命",
    "使命",
    "职责",
    "core mission",
    "your mission",
    "primary responsibilities",
    "responsibilities",
)
# Secondary markers used when no mission heading is found (e.g. odd templates).
_WORK_MARKERS = (
    "关键规则",
    "工作流程",
    "交付",
    "critical rules",
    "workflow",
    "deliverable",
)


@dataclass(frozen=True)
class PersonaTemplate:
    id: str  # filename without .md, e.g. "engineering-frontend-developer"
    lang: str  # "zh" | "en"
    category: str  # directory name, e.g. "engineering"
    name: str
    description: str
    emoji: str
    path: Path


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    yaml_text = text[3:end].lstrip("\n")
    body = text[end + 4 :].lstrip("\n")
    try:
        fm = yaml.safe_load(yaml_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def _normalize_heading(line: str) -> str:
    """Lowercase a heading line with leading #'s and emoji/punctuation stripped."""
    stripped = line.lstrip("#").strip()
    # Drop a leading emoji/symbol token if present (e.g. "🎯 Your Core Mission").
    return "".join(ch for ch in stripped if ch.isalnum() or ch.isspace() or "一" <= ch <= "鿿").strip().lower()


def _find_split_index(lines: list[str]) -> int | None:
    """Return the line index of the first mission/role heading, or None."""
    for markers in (_MISSION_MARKERS, _WORK_MARKERS):
        for i, line in enumerate(lines):
            if not line.startswith("## "):
                continue
            heading = _normalize_heading(line)
            if any(m in heading for m in markers):
                return i
    return None


def split_template(
    markdown: str,
    *,
    name: str,
    department: str,
    title: str | None,
) -> tuple[str, str]:
    """Map a template markdown onto (soul_md, role_md).

    SOUL = tianshu frontmatter (name/department/title) + personality body
    (everything before the first mission/role heading).
    ROLE = the mission/role heading onward.

    Fallback (no mission heading found): the whole body becomes ROLE and SOUL
    keeps the title line + intro paragraph, so neither file is empty.
    """
    _, body = _parse_frontmatter(markdown)
    body = body.strip()
    lines = body.splitlines()

    split = _find_split_index(lines)
    if split is not None:
        soul_body = "\n".join(lines[:split]).strip()
        role_body = "\n".join(lines[split:]).strip()
    else:
        # Fallback: intro (up to the first blank line after content) → SOUL.
        intro: list[str] = []
        for line in lines:
            if line.strip() == "" and intro:
                break
            intro.append(line)
        soul_body = "\n".join(intro).strip()
        role_body = body
        logger.warning(
            "split_template: no mission heading found; using intro/body fallback",
        )

    fm_lines = [f"name: {name}", f"department: {department}"]
    if title:
        fm_lines.append(f"title: {title}")
    frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n"

    soul_md = frontmatter + "\n" + soul_body + "\n"
    role_md = (role_body or f"# {name} — 职责\n") + "\n"
    return soul_md, role_md


class TemplateLibrary:
    """Scans the vendored template tree and serves templates by lang/id."""

    def __init__(self, templates_dir: Path) -> None:
        self._dir = Path(templates_dir)
        # lang -> {id: PersonaTemplate}
        self._index: dict[str, dict[str, PersonaTemplate]] = {}

    def load(self) -> None:
        self._index = {lang: {} for lang in LANGS}
        if not self._dir.is_dir():
            logger.warning("Persona templates dir not found: %s", self._dir)
            return
        for lang in LANGS:
            lang_dir = self._dir / lang
            if not lang_dir.is_dir():
                continue
            for category_dir in sorted(lang_dir.iterdir()):
                if not category_dir.is_dir():
                    continue
                for md in sorted(category_dir.glob("*.md")):
                    if md.name.lower().startswith("readme"):
                        continue
                    tmpl = self._parse_template(lang, category_dir.name, md)
                    if tmpl:
                        self._index[lang][tmpl.id] = tmpl
        total = sum(len(v) for v in self._index.values())
        logger.info("Loaded %d persona templates", total)

    def _parse_template(
        self, lang: str, category: str, path: Path,
    ) -> PersonaTemplate | None:
        try:
            fm, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            return None
        tid = path.stem
        return PersonaTemplate(
            id=tid,
            lang=lang,
            category=category,
            name=str(fm.get("name") or tid),
            description=str(fm.get("description") or ""),
            emoji=str(fm.get("emoji") or ""),
            path=path,
        )

    def list(self, lang: str) -> list[PersonaTemplate]:
        return sorted(
            self._index.get(lang, {}).values(),
            key=lambda t: (t.category, t.name),
        )

    def get(self, lang: str, template_id: str) -> PersonaTemplate | None:
        return self._index.get(lang, {}).get(template_id)

    def render(
        self,
        template: PersonaTemplate,
        *,
        name: str,
        department: str,
        title: str | None,
    ) -> tuple[str, str]:
        """Read the template file and return (soul_md, role_md)."""
        markdown = template.path.read_text(encoding="utf-8")
        return split_template(
            markdown, name=name, department=department, title=title,
        )
