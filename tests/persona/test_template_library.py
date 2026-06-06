"""Tests for the persona role-template library and SOUL/ROLE splitting."""

from __future__ import annotations

from tianshu.persona.template_library import (
    TemplateLibrary,
    split_template,
)

ZH_TEMPLATE = """\
---
name: 前端开发者
description: 前端专家
emoji: 💻
color: cyan
---

# 前端开发者 Agent 人格

你是 **前端开发者**。

## 你的身份与记忆
- 角色：专家

## 你的核心使命
- 构建 Web 应用

## 你必须遵循的关键规则
- 性能优先
"""

EN_TEMPLATE = """\
---
name: Frontend Developer
description: FE expert
emoji: 🖥️
---

# Frontend Developer Agent Personality

You are a **Frontend Developer**.

## 🧠 Your Identity & Memory
- Role: expert

## 🎯 Your Core Mission
- Build web apps
"""

NO_MISSION_TEMPLATE = """\
---
name: Odd One
---

# Intro line
some intro paragraph

## Other Section
body content here
"""


class TestSplitTemplate:
    def test_zh_split_at_mission(self):
        soul, role = split_template(
            ZH_TEMPLATE, name="测试官", department="wenyuan", title="参谋",
        )
        # SOUL keeps tianshu frontmatter + personality, before 核心使命.
        assert soul.startswith("---\n")
        assert "name: 测试官" in soul
        assert "department: wenyuan" in soul
        assert "title: 参谋" in soul
        assert "你的身份与记忆" in soul
        assert "你的核心使命" not in soul
        # ROLE starts at the mission section onward.
        assert role.lstrip().startswith("## 你的核心使命")
        assert "你必须遵循的关键规则" in role

    def test_en_split_handles_emoji_heading(self):
        soul, role = split_template(
            EN_TEMPLATE, name="Tester", department="wenyuan", title=None,
        )
        assert "Your Identity & Memory" in soul
        assert "Core Mission" not in soul
        assert "Your Core Mission" in role
        # No title line when title is None.
        frontmatter = soul.split("---", 2)[1]
        assert "title:" not in frontmatter

    def test_fallback_without_mission_heading(self):
        soul, role = split_template(
            NO_MISSION_TEMPLATE, name="N", department="d", title=None,
        )
        # Intro becomes SOUL; whole body becomes ROLE.
        assert "# Intro line" in soul
        assert "## Other Section" not in soul
        assert role.lstrip().startswith("# Intro line")
        assert "## Other Section" in role

    def test_original_frontmatter_stripped(self):
        soul, _ = split_template(
            ZH_TEMPLATE, name="测试官", department="wenyuan", title=None,
        )
        # The template's own emoji/color frontmatter must not leak through.
        assert "emoji:" not in soul
        assert "color:" not in soul


class TestTemplateLibrary:
    def _seed(self, root):
        (root / "zh" / "engineering").mkdir(parents=True)
        (root / "en" / "engineering").mkdir(parents=True)
        (root / "zh" / "engineering" / "engineering-frontend-developer.md").write_text(
            ZH_TEMPLATE, encoding="utf-8",
        )
        (root / "en" / "engineering" / "engineering-frontend-developer.md").write_text(
            EN_TEMPLATE, encoding="utf-8",
        )
        # README files in a category must be ignored.
        (root / "zh" / "engineering" / "README.md").write_text("# readme", encoding="utf-8")

    def test_load_and_list(self, tmp_path):
        self._seed(tmp_path)
        lib = TemplateLibrary(tmp_path)
        lib.load()
        zh = lib.list("zh")
        en = lib.list("en")
        assert len(zh) == 1  # README ignored
        assert len(en) == 1
        assert zh[0].id == "engineering-frontend-developer"
        assert zh[0].name == "前端开发者"
        assert zh[0].emoji == "💻"
        assert zh[0].category == "engineering"

    def test_get_and_render(self, tmp_path):
        self._seed(tmp_path)
        lib = TemplateLibrary(tmp_path)
        lib.load()
        tmpl = lib.get("zh", "engineering-frontend-developer")
        assert tmpl is not None
        soul, role = lib.render(
            tmpl, name="测试官", department="wenyuan", title=None,
        )
        assert "name: 测试官" in soul
        assert "你的核心使命" in role

    def test_get_missing_returns_none(self, tmp_path):
        self._seed(tmp_path)
        lib = TemplateLibrary(tmp_path)
        lib.load()
        assert lib.get("zh", "nonexistent") is None
        assert lib.get("fr", "engineering-frontend-developer") is None

    def test_missing_dir_is_safe(self, tmp_path):
        lib = TemplateLibrary(tmp_path / "does-not-exist")
        lib.load()
        assert lib.list("zh") == []
