# Skills Loader 三项增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 借鉴 hermes-agent 的 skills 加载经验，对 tianshu `SkillsLoader` 做三项无破坏性增强：① 跨进程磁盘 metadata snapshot，② SKILL.md 内容模板变量预处理，③ 用户级 `skills_disabled` 配置（含 persona 粒度）。

**Architecture：** 三个改动彼此独立，可并行实现。最终任务统一补测试与文档，符合"功能优先，测试最后补"项目偏好。

**Tech Stack:** Python 3.12+, Pydantic Settings, watchdog, frontmatter

**叙事对齐：** "宫殿运行更稳" + "主人保留否决权" —— 不动 `always:true` 全文注入、不动 SkillsWatcher、不动 metrics/guard 现有治理。

---

## File Map

| Task | Create | Modify |
|------|--------|--------|
| T1 Snapshot | — | `src/tianshu/skills/loader.py` |
| T2 Template Vars | `src/tianshu/skills/preprocess.py` | `src/tianshu/skills/loader.py`, `src/tianshu/persona/prompt_builder.py`, `src/tianshu/executor/agent.py` |
| T3 Disabled List | — | `src/tianshu/config.py`, `src/tianshu/skills/loader.py`, `src/tianshu/app.py` |
| T4 Tests + Docs | `tests/skills/test_snapshot.py`, `tests/skills/test_preprocess.py`, `tests/skills/test_disabled.py` | `docs/superpowers/specs/`（如需要新建对应 spec） |

---

## Task 1：跨进程磁盘 Metadata Snapshot

**目标：** `list_all_metadata()` cold path 后落盘 JSON，进程重启时若 manifest 校验通过则直接 hydrate `_l2_metadata` + `_l2_stats`，免冷扫描。

**Files:**
- Modify: `src/tianshu/skills/loader.py`

**关键边界：**
- snapshot 路径：`~/.tianshu/.skills_metadata_snapshot.json`（与现有 `~/.tianshu/skills/` 同级，不污染 skill 目录本身）
- snapshot 字段：`{schema_version, dirs: {builtin, user, workspace}, manifest: {path: [mtime_ns, size]}, metadata: [...]}`
- **dirs 字段** 用来防止不同 workspace 的 snapshot 相互污染：hydrate 前比对当前三个 dir 路径是否完全一致，不一致即视为失效。
- 同步删除策略：`SkillsWatcher` 触发 `invalidate_cache()` 时连带删除 snapshot 文件；`save_skill / create_skill / delete_skill` 同样。
- 失败容错：snapshot 解析任何异常都走 cold path，不抛错。

- [ ] **Step 1：定义 snapshot 路径常量与版本**

在 `loader.py` 顶部增补：

```python
_SNAPSHOT_VERSION = 1
_SNAPSHOT_FILENAME = ".skills_metadata_snapshot.json"
```

`SkillsLoader.__init__` 增加：

```python
# Snapshot 落地在 user_dir 的父目录（~/.tianshu/）
self._snapshot_path: Path | None = (
    self._user_dir.parent / _SNAPSHOT_FILENAME if self._user_dir else None
)
```

- [ ] **Step 2：新增 `_load_snapshot()` / `_write_snapshot()` 方法**

```python
def _load_snapshot(self) -> bool:
    """Hydrate _l2_metadata + _l2_stats from disk snapshot. Return True if hit."""
    if not self._snapshot_path or not self._snapshot_path.is_file():
        return False
    try:
        import json
        data = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if data.get("schema_version") != _SNAPSHOT_VERSION:
        return False
    # dirs 必须完全匹配当前 SkillsLoader 配置
    expected_dirs = {
        "builtin": str(self._builtin_dir),
        "user": str(self._user_dir) if self._user_dir else None,
        "workspace": str(self._workspace_dir) if self._workspace_dir else None,
    }
    if data.get("dirs") != expected_dirs:
        return False
    manifest_raw = data.get("manifest") or {}
    metadata = data.get("metadata") or []
    if not isinstance(metadata, list):
        return False
    # 校验 manifest 中每个文件 mtime/size 仍匹配磁盘
    stats: dict[str, tuple[int, int]] = {}
    for path_str, pair in manifest_raw.items():
        try:
            st = os.stat(path_str)
            mtime, size = pair
            if st.st_mtime_ns != mtime or st.st_size != size:
                return False
            stats[path_str] = (mtime, size)
        except OSError:
            return False
    self._l2_metadata = metadata
    self._l2_stats = stats
    return True

def _write_snapshot(self) -> None:
    if not self._snapshot_path or self._l2_metadata is None:
        return
    payload = {
        "schema_version": _SNAPSHOT_VERSION,
        "dirs": {
            "builtin": str(self._builtin_dir),
            "user": str(self._user_dir) if self._user_dir else None,
            "workspace": str(self._workspace_dir) if self._workspace_dir else None,
        },
        "manifest": {p: list(v) for p, v in self._l2_stats.items()},
        "metadata": self._l2_metadata,
    }
    try:
        import json
        _atomic_write(self._snapshot_path, json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.debug("Failed to write skills snapshot", exc_info=True)
```

- [ ] **Step 3：改写 `list_all_metadata()` 接入 snapshot**

```python
def list_all_metadata(self) -> list[dict]:
    # 内存命中：保持原逻辑
    if self._l2_metadata is not None and self._l2_stats_valid():
        return self._l2_metadata
    # 内存缺失：尝试磁盘 snapshot
    if self._l2_metadata is None and self._load_snapshot():
        return self._l2_metadata  # type: ignore[return-value]
    # 冷启动：完整扫描，扫完落盘
    result: list[dict] = []
    new_stats: dict[str, tuple[int, int]] = {}
    self._collect_metadata(self._builtin_dir, "builtin", result, new_stats)
    if self._user_dir and self._user_dir.is_dir():
        self._collect_metadata(self._user_dir, "user", result, new_stats)
    if self._workspace_dir:
        ws_skills = self._workspace_dir / "skills"
        if ws_skills.is_dir():
            self._collect_metadata(ws_skills, "workspace", result, new_stats)
    if hasattr(self, "_injected_skills"):
        for name, content in self._injected_skills.items():
            result.append({
                "name": name, "description": "", "source": "injected",
                "always": False, "tool_tier": None, "path": "",
                "content_length": len(content),
            })
    self._l2_metadata = result
    self._l2_stats = new_stats
    self._write_snapshot()
    return result
```

- [ ] **Step 4：`invalidate_cache` / 写操作清理 snapshot**

```python
def invalidate_cache(self) -> None:
    self._l1_cache.clear()
    self._l2_stats.clear()
    self._l2_metadata = None
    self._delete_snapshot()
    logger.debug("Skills cache invalidated")

def _delete_snapshot(self) -> None:
    if self._snapshot_path and self._snapshot_path.exists():
        try:
            self._snapshot_path.unlink()
        except OSError:
            pass
```

`save_skill / create_skill / delete_skill` 末尾对 `self._l2_metadata = None` 之后追加 `self._delete_snapshot()`。

---

## Task 2：SKILL 内容模板变量预处理

**目标：** 在 `get_skill()` / `load_always()` / `load_all()` 返回 content 时，把 `${TIANSHU_SKILL_DIR}` `${TIANSHU_WORKSPACE_DIR}` `${TIANSHU_USER_SKILLS_DIR}` `${TIANSHU_PERSONA}` 替换成运行时值。**未提供值的 token 保留原样**（hermes 同款策略，便于排错）。

**Files:**
- Create: `src/tianshu/skills/preprocess.py`
- Modify:
  - `src/tianshu/skills/loader.py`：所有返回 content 的入口接 `persona: str | None = None` 参数，调用前置预处理；`_l1_cache` 仍存原始内容
  - `src/tianshu/persona/prompt_builder.py:180,188,329,333`：调用 `load_index/load_always` 时把 `persona.id` 传下去
  - `src/tianshu/executor/agent.py:546,550`：同样传 persona id（无 persona 时传 None）

**关键边界：**
- 预处理只动 body，不动 frontmatter，因此放在 loader 解析后、返回前。
- L1 cache 里存**原始 content**，调用 `get_skill()` 时按 persona 上下文做一次 substitute；这样不同 persona 不会互相污染缓存。
- `load_index()` 不需要预处理（只输出 name+description），`load_always()` / `load_all()` / `get_skill()` 需要。
- `description` 字段不替换（保留搜索/匹配原义）。

- [ ] **Step 1：新建 `preprocess.py`**

```python
"""Skill content preprocessing — runtime template variable substitution."""
from __future__ import annotations

import re
from pathlib import Path

_TEMPLATE_RE = re.compile(
    r"\$\{(TIANSHU_SKILL_DIR|TIANSHU_WORKSPACE_DIR|TIANSHU_USER_SKILLS_DIR|TIANSHU_PERSONA)\}"
)


def substitute_template_vars(
    content: str,
    *,
    skill_dir: Path | None = None,
    workspace_dir: Path | None = None,
    user_skills_dir: Path | None = None,
    persona: str | None = None,
) -> str:
    """Replace ${TIANSHU_*} tokens. Unresolved tokens are left in place."""
    if not content or "${TIANSHU_" not in content:
        return content

    mapping: dict[str, str | None] = {
        "TIANSHU_SKILL_DIR": str(skill_dir) if skill_dir else None,
        "TIANSHU_WORKSPACE_DIR": str(workspace_dir) if workspace_dir else None,
        "TIANSHU_USER_SKILLS_DIR": str(user_skills_dir) if user_skills_dir else None,
        "TIANSHU_PERSONA": persona,
    }

    def _replace(match: re.Match) -> str:
        token = match.group(1)
        value = mapping.get(token)
        return value if value is not None else match.group(0)

    return _TEMPLATE_RE.sub(_replace, content)
```

- [ ] **Step 2：`SkillsLoader` 接预处理**

新增私有方法 `_preprocess_content(content, skill_path, persona)`：

```python
def _preprocess_content(
    self, content: str, skill_path: str | Path | None, persona: str | None
) -> str:
    from tianshu.skills.preprocess import substitute_template_vars
    skill_dir = Path(skill_path).parent if skill_path else None
    return substitute_template_vars(
        content,
        skill_dir=skill_dir,
        workspace_dir=self._workspace_dir,
        user_skills_dir=self._user_dir,
        persona=persona,
    )
```

`get_skill(name)` 改为 `get_skill(name, persona: str | None = None)`：在返回 dict 前对 `result["content"]` 调一次 `_preprocess_content`，但**写入 L1 cache 用原始内容**（不要把已替换的内容缓存掉），调用方按需 substitute。

实现做法：L1 仍存包含原始 content 的 dict；返回时 `dict(result, content=preprocessed)`。

`load_always(filter_names, persona=None)`、`load_all(filter_names, persona=None)`：循环里对每个 skill content 调用 `_preprocess_content`，传入对应 SKILL.md 路径作为 `skill_dir` 来源。

- [ ] **Step 3：调用方传入 persona id**

`src/tianshu/persona/prompt_builder.py`：

```python
# Line ~188
always_text = self._skills.load_always(
    filter_names=filter_names,
    persona=persona.id if persona else None,
)
```

`src/tianshu/executor/agent.py:550` 同步调整（无 persona 时传 None）。

`src/tianshu/gateway/api.py:1845, 1887` 的 `loader.get_skill(name)` 不传 persona（admin/UI 视图希望看原始 content，方便编辑），保持原样。

---

## Task 3：`skills_disabled` 配置 + persona 粒度

**目标：** 用户可在 `TIANSHU_SKILLS_DISABLED` 环境变量或 `.env` 中声明全局禁用清单；persona 可在自身定义里追加禁用清单。被禁用的 skill 不出现在 `load_index/load_always/load_all/list_all_metadata`，`get_skill` 返回 None。

**Files:**
- Modify:
  - `src/tianshu/config.py`：新增 `skills_disabled` 字段
  - `src/tianshu/skills/loader.py`：构造函数接 `disabled_skills`，过滤逻辑
  - `src/tianshu/app.py:126`：把 settings.skills_disabled 解析后传入
  - `src/tianshu/persona/prompt_builder.py`：persona 级禁用合并

**关键边界：**
- 全局禁用：逗号分隔字符串（与 `feishu_allowed_users` 同款风格），首尾去空、忽略空项。
- Persona 级禁用：复用现有 `AgentPersona.skills_allowed`（白名单）相反方向 —— 新增 `skills_disabled: list[str] = []` 字段，prompt_builder 调用时合并 global + persona 两层 disabled set。
- 写操作不受禁用影响：用户仍能 `skill_manage(action='edit')` 调整被禁用的 skill（避免"自锁"）。
- `create_skill / save_skill / delete_skill / patch_skill` 不过滤；只过滤"读取注入"路径。

- [ ] **Step 1：新增 settings 字段**

`src/tianshu/config.py:28` 之后追加：

```python
skills_disabled: str = ""  # 逗号分隔；e.g. "old-skill,deprecated-skill"
```

- [ ] **Step 2：`SkillsLoader` 接收 disabled 集合**

`__init__` 新增参数：

```python
def __init__(
    self,
    builtin_dir: Path,
    workspace_dir: Path | None = None,
    user_dir: Path | None = None,
    char_budget: int = 30000,
    disabled_skills: set[str] | None = None,
) -> None:
    ...
    self._disabled_skills: set[str] = set(disabled_skills or set())
```

新增运行时接口（供 prompt_builder 临时叠加 persona 级 disabled）：

```python
def with_extra_disabled(self, extra: set[str]) -> "SkillsLoader":
    """Return a lightweight view that adds extra disabled names on top of global."""
    # 简化：提供 contextual filter via 参数即可，不必真的 clone
    raise NotImplementedError  # 见 Step 3 的实际方案
```

实际方案：所有读取入口接受 `extra_disabled: set[str] | None = None`，内部 `effective = self._disabled_skills | (extra_disabled or set())`。覆盖：

```python
def load_index(self, filter_names=None, include_dormant=False, metrics_store=None,
               extra_disabled: set[str] | None = None) -> str:
    metadata = self.list_all_metadata()
    blocked = self._disabled_skills | (extra_disabled or set())
    if blocked:
        metadata = [m for m in metadata if m["name"] not in blocked]
    ...
```

`load_always / load_all / get_skill` 同款过滤。`list_all_metadata` 内部不过滤（保留全集供其它治理逻辑使用），过滤动作放在调用方。

- [ ] **Step 3：`app.py` 解析并注入**

`src/tianshu/app.py:126` 之前增加解析：

```python
disabled_raw = (settings.skills_disabled or "").strip()
disabled_skills = {
    s.strip() for s in disabled_raw.split(",") if s.strip()
}
skills = SkillsLoader(
    builtin_dir=builtin_skills_dir,
    workspace_dir=workspace_path,
    user_dir=user_skills_dir,
    char_budget=settings.skills_char_budget,
    disabled_skills=disabled_skills,
)
```

- [ ] **Step 4：persona 级禁用**

`src/tianshu/persona/persona.py`（或 `models/persona.py`，看现有定义位置）的 `AgentPersona` 增加：

```python
skills_disabled: list[str] = field(default_factory=list)
```

`prompt_builder.py:180`：

```python
extra = set(persona.skills_disabled) if persona and persona.skills_disabled else None
index_text = self._skills.load_index(
    filter_names=filter_names,
    metrics_store=self._metrics_store,
    extra_disabled=extra,
)
always_text = self._skills.load_always(
    filter_names=filter_names,
    persona=persona.id if persona else None,
    extra_disabled=extra,
)
```

---

## Task 4：测试 + 文档（按"功能优先，测试最后补"项目偏好集中处理）

**Files:**
- Create:
  - `tests/skills/test_snapshot.py`
  - `tests/skills/test_preprocess.py`
  - `tests/skills/test_disabled.py`

- [ ] **Snapshot 测试**
  - 第一次 `list_all_metadata` 后 snapshot 文件出现
  - 重建一个新 `SkillsLoader` 指向相同目录，命中 snapshot（断言 `_collect_metadata` 不被调用 —— 用 monkeypatch 计数）
  - mtime/size 任一变化 → 失效，回到 cold path
  - workspace_dir 变化 → 不复用旧 snapshot
  - `invalidate_cache()` / `save_skill` / `create_skill` / `delete_skill` 后 snapshot 文件被删除

- [ ] **Preprocess 测试**
  - `${TIANSHU_SKILL_DIR}` 替换为 SKILL.md 所在目录
  - `${TIANSHU_WORKSPACE_DIR}` 在 workspace 未配置时保留原 token
  - `${TIANSHU_PERSONA}` 在 persona=None 时保留原 token，传值时替换
  - `load_always` 多 skill 各自独立替换 SKILL_DIR
  - L1 cache 命中后不会出现"上一次替换结果污染下一次"的问题（不同 persona 调 `get_skill` 同名 skill 各得各的）

- [ ] **Disabled 测试**
  - 全局禁用：被禁用的 skill 不在 `load_index` / `load_always` / `load_all` 中
  - `get_skill(name)` 对被禁用 skill 返回 None
  - `save_skill / create_skill / delete_skill` 不受禁用影响（仍可写）
  - persona 级禁用叠加全局禁用
  - 被禁用 + `always:true` 也被屏蔽（禁用优先级高于 always）

- [ ] **手动验证清单**
  - `python -c "from tianshu.skills.loader import SkillsLoader; ..."` 启动两次，看第二次冷启动时间差
  - 在 builtin/file-ops/SKILL.md 临时插入 `${TIANSHU_SKILL_DIR}` 看渲染结果
  - `.env` 加 `TIANSHU_SKILLS_DISABLED=file-ops`，启动后访问 `/api/skills` 确认被屏蔽

- [ ] **文档**
  - `README.md` 配置章节追加 `TIANSHU_SKILLS_DISABLED` 说明
  - `docs/superpowers/specs/` 视情况补一份 `2026-05-06-skills-loader-enhancements.md` spec 简短说明三项能力（如未来需要演进时方便溯源）

---

## 风险与回滚

| 改动 | 风险 | 回滚 |
|------|------|------|
| Snapshot | JSON 损坏导致 hydrate 失败 | `_load_snapshot` 任何异常返回 False，自动回到 cold path |
| Preprocess | 模板替换误伤 SKILL 中无意义的 `${...}` | 正则严格匹配 `TIANSHU_*` 前缀，其它 `${...}` 不动 |
| Disabled | 用户禁用了关键 skill 导致 emperor 行为退化 | 改回 `.env` 即可；无需代码回滚 |

## 验收标准

- 重启进程后第二次启动 `list_all_metadata` 不触发完整磁盘扫描（计时或 monkeypatch 验证）
- SKILL.md 内 `${TIANSHU_SKILL_DIR}` 被替换为绝对路径，`${TIANSHU_PERSONA}` 在 emperor/六部分别看到自己 id
- `.env` 加 `TIANSHU_SKILLS_DISABLED=foo` 后，`foo` 不出现在系统提示词
- 现有 `tests/test_skills_*.py` 全部通过（无回归）
