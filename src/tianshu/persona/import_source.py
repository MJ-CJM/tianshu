"""从外部 coding agent(openclaw / hermes)导入配置作百官(内臣)种子。

**一次性 seed,非 sync**:只取可移植的**人格 + 能力**(SOUL 人格 / 职责 / 技能 /
模型偏好),映射成天枢自己的 persona;此后该 persona 独立演化、受京察治理。
**明确排除运行态**——记忆 / 用户模型 / 学习历史 / 渠道 / 网关(那是运行态 / 自进化 /
外部机构,导入会与天枢自进化冲突)。见 docs/design/domain-model.md「执行主体本体论」。

三边一致点(官方文档):openclaw / hermes / 天枢都用 `SOUL.md` 定义人格;技能都是
agentskills.io `SKILL.md`(天枢 SkillsLoader 原生兼容)。故映射几乎零摩擦。

宽容解析:字段缺失降级不崩(源版本演进 + 用户配置不全);缺 SOUL.md 报可读错误。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)


@dataclass
class ImportSkillRef:
    """检测到的可导入技能(agentskills.io SKILL.md)。"""

    name: str
    source_dir: str
    description: str = ""


@dataclass
class PersonaImportDraft:
    """导入草稿:预填百官创建表单用。soul/role 是**裸内容**(create 时注天枢 frontmatter)。"""

    source: str
    soul_body: str
    role_body: str
    suggested_name: str
    suggested_model: str | None
    skills: list[ImportSkillRef] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)


class PersonaImportError(Exception):
    """导入失败的可读错误(缺 SOUL.md 等)。"""


_DEFAULT_PATHS = {"hermes": "~/.hermes", "openclaw": "~/.openclaw/workspace"}

# 明确排除:运行态 / 自进化 / 外部机构 —— 不导入(透明写进 source_notes)。
_EXCLUDE = {
    "hermes": [
        "memories/ 长期记忆",
        "USER.md 用户档案 / Honcho 用户模型",
        "自进化学习历史(guard_agent_created 等)",
    ],
    "openclaw": [
        "channels.* 渠道连接(WhatsApp/Telegram…)",
        "gateway.* 网关 / 配对状态",
        "MEMORY.md 运行记忆",
    ],
}


def detect_default_path(source: str) -> Path | None:
    """探测默认配置目录;不存在返回 None。"""
    raw = _DEFAULT_PATHS.get(source)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def import_from(source: str, path: Path) -> PersonaImportDraft:
    if source == "hermes":
        return _import_hermes(path)
    if source == "openclaw":
        return _import_openclaw(path)
    raise PersonaImportError(f"未知导入来源: {source!r}")


# --- 解析辅助 ---


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _tolerant_json(text: str) -> dict:
    """容错读 openclaw.json(标准 JSON;剥注释/尾逗号以防用户手写注释),失败返回 {}。"""
    if not text.strip():
        return {}
    no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    no_line = re.sub(r"(^|\s)//.*$", r"\1", no_block, flags=re.MULTILINE)
    no_trailing = re.sub(r",(\s*[}\]])", r"\1", no_line)
    try:
        result = json.loads(no_trailing)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _extract_openclaw_model(node: object) -> str | None:
    """openclaw model 可为 "provider/model" 字符串,或 {primary, fallbacks}(源 schema)。"""
    if isinstance(node, str):
        return node or None
    if isinstance(node, dict):
        primary = node.get("primary")
        return str(primary) if primary else None
    return None


def _extract_hermes_model(node: object) -> str | None:
    """hermes model 三形态(据 hermes cli.py 归一化):裸字符串 / {default:} / {model:} 别名键。"""
    if isinstance(node, dict):
        val = node.get("default") or node.get("model")  # 覆盖 default 与 model 两个键名
        return str(val) if val else None
    if isinstance(node, str):
        return node.strip() or None
    return None


def _select_openclaw_agent(agents: dict) -> dict | None:
    """openclaw 是多助理架构;人格种子取 default 条目(无则第一条)的 model/skills。"""
    entries = agents.get("list")
    if not isinstance(entries, list):
        return None
    dicts = [a for a in entries if isinstance(a, dict)]
    if not dicts:
        return None
    return next((a for a in dicts if a.get("default")), dicts[0])


def _scan_skills(skills_root: Path) -> list[ImportSkillRef]:
    """扫 agentskills.io SKILL.md,读 frontmatter 的 name/description。"""
    out: list[ImportSkillRef] = []
    if not skills_root.is_dir():
        return out
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        if not skill_md.is_file():
            continue
        try:
            post = frontmatter.load(str(skill_md))
        except Exception:  # noqa: BLE001 — 单个技能坏 frontmatter 不该拖垮导入
            logger.warning("import: 跳过坏 SKILL.md %s", skill_md)
            continue
        name = str(post.get("name") or skill_md.parent.name)
        out.append(
            ImportSkillRef(
                name=name,
                source_dir=str(skill_md.parent),
                description=str(post.get("description") or ""),
            )
        )
    return out


def _guess_name(soul_md: str, fallback_dir: Path) -> str:
    """人格名:优先 SOUL.md frontmatter name → 第一个一级标题 → 目录名。"""
    try:
        post = frontmatter.loads(soul_md)
        if post.get("name"):
            return str(post["name"])
    except Exception:  # noqa: BLE001
        pass
    heading = re.search(r"^#\s+(.+)$", soul_md, re.MULTILINE)
    if heading:
        return heading.group(1).strip()
    return fallback_dir.name


def _strip_frontmatter(md: str) -> str:
    """去掉源 SOUL.md 自己的 frontmatter,只留正文(天枢会注自己的 frontmatter)。"""
    try:
        return frontmatter.loads(md).content.strip()
    except Exception:  # noqa: BLE001
        return md.strip()


# --- 各来源适配器 ---


def _import_hermes(home: Path) -> PersonaImportDraft:
    soul_raw = _read_text(home / "SOUL.md")
    if not soul_raw.strip():
        raise PersonaImportError(f"未找到 hermes SOUL.md（{home}/SOUL.md）")
    soul_body = _strip_frontmatter(soul_raw)
    # hermes SOUL.md 自含人格+职责;AGENTS.md 若有则并入职责段。
    role_body = _strip_frontmatter(_read_text(home / "AGENTS.md"))

    model = None
    notes = [f"已排除(运行态/自进化,不导入): {x}" for x in _EXCLUDE["hermes"]]
    cfg_text = _read_text(home / "config.yaml")
    if cfg_text.strip():
        import yaml

        try:
            cfg = yaml.safe_load(cfg_text) or {}
        except yaml.YAMLError:
            cfg = {}
            logger.warning("import: hermes config.yaml 解析失败,跳过模型/工具提取")
        if isinstance(cfg, dict):
            # model 三形态兼容(裸字符串旧代码会 crash,{model:} 别名会静默丢失)
            model = _extract_hermes_model(cfg.get("model"))
            agent_cfg_raw = cfg.get("agent")
            agent_cfg = agent_cfg_raw if isinstance(agent_cfg_raw, dict) else {}
            disabled = agent_cfg.get("disabled_toolsets") or []
            if disabled:
                notes.append(
                    f"hermes 禁用工具集(仅提示,未自动映射): {', '.join(map(str, disabled))}"
                )
            # personalities:hermes 特有的命名多人格预设(运行态切换),v1 不导入,透明提示
            personalities = agent_cfg.get("personalities")
            if isinstance(personalities, dict) and personalities:
                names = ", ".join(map(str, list(personalities)[:8]))
                notes.append(
                    f"检测到 {len(personalities)} 个 personalities 预设(命名多人格,未导入;"
                    f"可后续各 seed 成独立官员): {names}"
                )
            # auxiliary/vision/compression/subagent 模型档均运行态 / side-task,不导入
            if cfg.get("auxiliary"):
                notes.append("hermes auxiliary/vision/compression/subagent 模型档均运行态,未导入")

    skills = _scan_skills(home / "skills")
    return PersonaImportDraft(
        source="hermes",
        soul_body=soul_body,
        role_body=role_body,
        suggested_name=_guess_name(soul_raw, home),
        suggested_model=str(model) if model else None,
        skills=skills,
        source_notes=notes,
    )


def _import_openclaw(workspace: Path) -> PersonaImportDraft:
    soul_raw = _read_text(workspace / "SOUL.md")
    if not soul_raw.strip():
        raise PersonaImportError(f"未找到 openclaw SOUL.md（{workspace}/SOUL.md）")
    soul_body = _strip_frontmatter(soul_raw)
    # 职责:AGENTS.md(工作规则/红线)+ IDENTITY.md(身份卡)
    agents_md = _strip_frontmatter(_read_text(workspace / "AGENTS.md"))
    identity_md = _strip_frontmatter(_read_text(workspace / "IDENTITY.md"))
    role_body = "\n\n".join(p for p in (agents_md, identity_md) if p).strip()

    notes = [f"已排除(运行态/机构,不导入): {x}" for x in _EXCLUDE["openclaw"]]
    # openclaw.json(标准 JSON)在 workspace 父目录(~/.openclaw/openclaw.json)。
    # openclaw 是多助理架构:模型偏好优先取 default agent 条目,回退 agents.defaults。
    # model 可为字符串或 {primary, fallbacks},两者都兼容(否则字符串形式会 crash)。
    cfg = _tolerant_json(_read_text(workspace.parent / "openclaw.json"))
    agents_raw = cfg.get("agents")
    agents = agents_raw if isinstance(agents_raw, dict) else {}
    chosen = _select_openclaw_agent(agents)
    model = _extract_openclaw_model(chosen.get("model")) if chosen else None
    if model is None:
        defaults_raw = agents.get("defaults")
        defaults = defaults_raw if isinstance(defaults_raw, dict) else {}
        model = _extract_openclaw_model(defaults.get("model"))
    # 白名单技能:bundled 技能在 openclaw 安装目录(非用户配置目录),无法解析全文 → 透明提示。
    if chosen and isinstance(chosen.get("skills"), list) and chosen["skills"]:
        notes.append(
            "agents.list 技能白名单(bundled 技能在 openclaw 安装目录,未解析全文,"
            f"仅导入 workspace 本地技能): {', '.join(map(str, chosen['skills']))}"
        )
    # AGENTS.md 常含运行手册样板(记忆规则/群聊礼仪/心跳),已整段进 ROLE,提示用户精简。
    if agents_md:
        notes.append("AGENTS.md 可能含运行手册样板(记忆/群聊/心跳),请在 ROLE 预览里精简为职责")
    if _read_text(workspace / "TOOLS.md").strip():
        notes.append("openclaw TOOLS.md 已作参考(工具需手动映射到天枢权限)")

    skills = _scan_skills(workspace / "skills")
    return PersonaImportDraft(
        source="openclaw",
        soul_body=soul_body,
        role_body=role_body,
        suggested_name=_guess_name(soul_raw, workspace),
        suggested_model=str(model) if model else None,
        skills=skills,
        source_notes=notes,
    )
