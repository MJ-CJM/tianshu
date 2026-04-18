"""Render PROFILE.md from synthesis sections + preserve manual notes."""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone

from tianshu.persona.profile_schema import (
    AUTO_SECTION_MARKER,
    ProfileFrontmatter,
    ProfileSections,
    parse_profile,
)

logger = logging.getLogger(__name__)

MANUAL_DIFF_CONFLICT_THRESHOLD = 0.30


def render_auto_section(
    persona_name: str,
    window_days: int,
    last_synthesized: str,
    sections: ProfileSections,
) -> str:
    header = (
        f"# {persona_name} · 成长档案\n\n"
        f"> 由 ProfileSynthesizer 基于近 {window_days} 天任务与记忆合成。"
        f"最后更新:{last_synthesized}。\n"
    )
    return "\n\n".join(
        p for p in [
            header,
            "## 擅长领域\n" + (sections.specialties_md or "(数据不足)"),
            "## 近期任务分布(" + str(window_days) + " 天)\n" + (
                sections.task_distribution_md or "(数据不足)"
            ),
            "## 健康度\n" + (sections.health_md or "(数据不足)"),
            "## 退化迹象\n" + (sections.degradations_md or "(暂无)"),
        ] if p
    )


def render_markdown(
    frontmatter: ProfileFrontmatter,
    auto_section: str,
    manual_section: str,
) -> str:
    fm_yaml = frontmatter.to_yaml()
    parts = [
        "---",
        fm_yaml,
        "---",
        "",
        auto_section.strip(),
        "",
        AUTO_SECTION_MARKER,
        "",
        "## 手写备注(synthesizer 不覆盖)",
        "",
        manual_section.strip(),
    ]
    return "\n".join(p for p in parts if p is not None).rstrip() + "\n"


def auto_section_diff_ratio(prev_auto: str, new_auto: str) -> float:
    """Return 1 - similarity_ratio (higher = more changed)."""
    if not prev_auto:
        return 0.0
    matcher = difflib.SequenceMatcher(None, prev_auto, new_auto, autojunk=False)
    return 1.0 - matcher.ratio()


def detect_manual_section(prev_markdown: str) -> tuple[str, bool]:
    """Return (manual_section, manually_edited). Manually edited when non-empty."""
    if not prev_markdown:
        return "", False
    _, _, manual = parse_profile(prev_markdown)
    return manual, bool(manual.strip())
