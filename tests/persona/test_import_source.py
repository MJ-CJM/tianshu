"""从 openclaw/hermes 导入配置作百官种子:适配器解析 + 排除运行态断言。

一次性 seed(非 sync):只取人格+能力(SOUL/职责/技能/模型),排除记忆/学习/渠道。"""

import pytest

from tianshu.persona.import_source import (
    PersonaImportError,
    _tolerant_json,
    import_from,
)


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / ".hermes"
    _write(home / "SOUL.md", "---\nname: 赫尔墨斯\n---\n\n你是一个务实的工程助手,重视验证。")
    _write(
        home / "config.yaml",
        "model:\n  default: anthropic/claude-opus-4\nagent:\n  disabled_toolsets: [web, memory]\n",
    )
    _write(
        home / "skills" / "devops" / "SKILL.md",
        "---\nname: ci-runner\ndescription: run CI\n---\n# CI\n...",
    )
    # 应被排除的运行态
    _write(home / "USER.md", "用户档案:张三,偏好简洁")
    _write(home / "memories" / "m1.md", "记忆内容")
    return home


@pytest.fixture
def openclaw_workspace(tmp_path):
    base = tmp_path / ".openclaw"
    ws = base / "workspace"
    _write(ws / "SOUL.md", "# 助手\n你是我的个人助理,风格友好。")
    _write(ws / "AGENTS.md", "## 职责\n负责日程与提醒。")
    _write(ws / "IDENTITY.md", "名字叫小助手。")
    _write(
        base / "openclaw.json",
        '{\n  // primary model\n  "agents": { "defaults": { "model": { "primary": "anthropic/claude-opus-4-8" } } },\n}',
    )
    _write(ws / "TOOLS.md", "可用工具清单")
    # 应被排除的运行态(渠道)——放在 base(openclaw.json 的 channels),这里仅验证不进 draft
    return ws


class TestHermesImport:
    def test_extracts_soul_model_skills(self, hermes_home):
        draft = import_from("hermes", hermes_home)
        assert draft.source == "hermes"
        assert "务实的工程助手" in draft.soul_body
        assert "name: 赫尔墨斯" not in draft.soul_body  # 源 frontmatter 被剥离
        assert draft.suggested_name == "赫尔墨斯"
        assert draft.suggested_model == "anthropic/claude-opus-4"
        assert [s.name for s in draft.skills] == ["ci-runner"]

    def test_excludes_runtime_state(self, hermes_home):
        draft = import_from("hermes", hermes_home)
        # 记忆/用户档案不进 soul/role;排除项写进 source_notes(透明)
        assert "张三" not in draft.soul_body and "张三" not in draft.role_body
        assert "记忆内容" not in draft.soul_body
        joined = " ".join(draft.source_notes)
        assert "memories" in joined and "USER.md" in joined

    def test_disabled_toolsets_noted_not_mapped(self, hermes_home):
        draft = import_from("hermes", hermes_home)
        assert any("禁用工具集" in n and "web" in n for n in draft.source_notes)

    def test_model_string_form_no_crash(self, tmp_path):
        # hermes "New format" 裸字符串;旧实现 .get("default") 会 AttributeError
        home = tmp_path / ".hermes"
        _write(home / "SOUL.md", "# A\n人格")
        _write(home / "config.yaml", 'model: "anthropic/claude-opus-4.6"\n')
        draft = import_from("hermes", home)
        assert draft.suggested_model == "anthropic/claude-opus-4.6"

    def test_model_alias_key(self, tmp_path):
        # dict 只写 model: 别名键(无 default),旧实现静默丢失
        home = tmp_path / ".hermes"
        _write(home / "SOUL.md", "# A\n人格")
        _write(home / "config.yaml", "model:\n  model: anthropic/claude-sonnet-4\n")
        draft = import_from("hermes", home)
        assert draft.suggested_model == "anthropic/claude-sonnet-4"

    def test_personalities_and_auxiliary_noted(self, tmp_path):
        home = tmp_path / ".hermes"
        _write(home / "SOUL.md", "# A\n人格")
        _write(
            home / "config.yaml",
            "agent:\n  personalities:\n    pirate: 说话像海盗\n    noir: 冷硬派\n"
            "auxiliary:\n  vision:\n    model: anthropic/vision\n",
        )
        draft = import_from("hermes", home)
        joined = " ".join(draft.source_notes)
        assert "personalities" in joined and "pirate" in joined
        assert "auxiliary" in joined

    def test_missing_soul_raises(self, tmp_path):
        with pytest.raises(PersonaImportError, match="SOUL.md"):
            import_from("hermes", tmp_path / "empty")


class TestOpenClawImport:
    def test_extracts_soul_role_model(self, openclaw_workspace):
        draft = import_from("openclaw", openclaw_workspace)
        assert draft.source == "openclaw"
        assert "个人助理" in draft.soul_body
        assert "负责日程" in draft.role_body  # AGENTS.md → 职责
        assert "小助手" in draft.role_body  # IDENTITY.md → 职责
        # 标准 JSON(容错剥手写注释)解析出模型(对象形式 {primary})
        assert draft.suggested_model == "anthropic/claude-opus-4-8"

    def test_excludes_channels_gateway(self, openclaw_workspace):
        draft = import_from("openclaw", openclaw_workspace)
        joined = " ".join(draft.source_notes)
        assert "channels" in joined and "gateway" in joined

    def test_model_string_form_no_crash(self, tmp_path):
        # openclaw model 可为纯字符串;旧实现 .get("primary") 会 AttributeError
        base = tmp_path / ".openclaw"
        ws = base / "workspace"
        _write(ws / "SOUL.md", "# A\n人格")
        _write(base / "openclaw.json", '{"agents": {"defaults": {"model": "anthropic/claude-sonnet-4-5"}}}')
        draft = import_from("openclaw", ws)
        assert draft.suggested_model == "anthropic/claude-sonnet-4-5"

    def test_list_agent_model_preferred_over_defaults(self, tmp_path):
        # 多助理架构:default agent 条目的 model 优先于 agents.defaults,且白名单技能被透明提示
        base = tmp_path / ".openclaw"
        ws = base / "workspace"
        _write(ws / "SOUL.md", "# A\n人格")
        _write(
            base / "openclaw.json",
            '{"agents": {'
            '"defaults": {"model": "anthropic/fallback"},'
            '"list": ['
            '{"id": "chat", "model": "anthropic/other"},'
            '{"id": "home", "default": true, "model": {"primary": "anthropic/home-model"}, '
            '"skills": ["weather", "spotify"]}'
            ']}}',
        )
        draft = import_from("openclaw", ws)
        assert draft.suggested_model == "anthropic/home-model"
        assert any("weather" in n and "白名单" in n for n in draft.source_notes)

    def test_agents_md_boilerplate_noted(self, openclaw_workspace):
        draft = import_from("openclaw", openclaw_workspace)
        assert any("运行手册" in n for n in draft.source_notes)


class TestTolerantJson:
    def test_strips_comments_and_trailing_commas(self):
        text = '{\n  // line comment\n  "a": 1, /* block */\n  "b": [1,2,],\n}'
        assert _tolerant_json(text) == {"a": 1, "b": [1, 2]}

    def test_bad_json_returns_empty(self):
        assert _tolerant_json("not json at all {{{") == {}


class TestUnknownSource:
    def test_unknown_source_raises(self, tmp_path):
        with pytest.raises(PersonaImportError, match="未知导入来源"):
            import_from("grok", tmp_path)
