"""官员/部门/角色模板/铨选路由：personas / persona-templates / departments / routing。无统一 prefix，路径写全。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from tianshu.config_manager import ConfigManager
from tianshu.models import ApiResponse
from tianshu.persona.evaluator import PerformanceEvaluator
from tianshu.persona.selector import OfficialSelector
from tianshu.persona.template_library import TemplateLibrary
from tianshu.storage import Storage

personas_router = APIRouter(tags=["personas"])


# --- Department endpoints ---


@personas_router.get("/departments")
def list_departments(request: Request):
    storage: Storage = request.app.state.storage
    departments = storage.list_departments()
    return ApiResponse(success=True, data=departments)


@personas_router.post("/departments", response_model=ApiResponse, status_code=201)
async def create_department(request: Request):
    storage: Storage = request.app.state.storage
    body = await request.json()
    if not body.get("id") or not body.get("name"):
        raise HTTPException(status_code=400, detail="id and name are required")
    if storage.get_department(body["id"]):
        raise HTTPException(status_code=409, detail=f"Department '{body['id']}' already exists")
    storage.save_department(body)
    dept = storage.get_department(body["id"])
    return ApiResponse(success=True, data=dept)


@personas_router.put("/departments/{dept_id}", response_model=ApiResponse)
async def update_department(dept_id: str, request: Request):
    storage: Storage = request.app.state.storage
    existing = storage.get_department(dept_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
    body = await request.json()
    storage.update_department(dept_id, **body)
    updated = storage.get_department(dept_id)
    return ApiResponse(success=True, data=updated)


@personas_router.delete("/departments/{dept_id}", response_model=ApiResponse)
def delete_department(dept_id: str, request: Request):
    storage: Storage = request.app.state.storage
    existing = storage.get_department(dept_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Department '{dept_id}' not found")
    deleted = storage.delete_department(dept_id)
    if not deleted:
        raise HTTPException(
            status_code=409, detail="Cannot delete department with assigned personas"
        )
    return ApiResponse(success=True, data={"id": dept_id})


# --- Persona endpoints (Phase 3) ---


_TITLE_MAX_LEN = 32


def _render_persona_identity_files(
    runtime_dir: Path,
    persona_id: str,
    name: str,
    department: str,
    title: str | None,
    dept_label: str,
    department_template_dir: Path | None,
    *,
    overwrite: bool,
) -> tuple[Path, Path]:
    """渲染个性化 SOUL.md / ROLE.md 到 runtime_dir。

    - 不再无脑拷贝部门 SOUL.md。
    - 基于 name/department/title 渲染独立身份段；部门 SOUL.md/ROLE.md（若存在）作为"风格指引"附在末尾。
    - overwrite=False 时若文件已存在则跳过（用于 create）；True 时强制重写（用于 regenerate）。
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    soul_path = runtime_dir / "SOUL.md"
    role_path = runtime_dir / "ROLE.md"

    title_norm = (title or "").strip()
    title_clause = f"，担任 **{title_norm}** 一职" if title_norm else ""
    dept_soul_text = ""
    dept_role_text = ""
    if department_template_dir is not None:
        dept_soul = department_template_dir / "SOUL.md"
        dept_role = department_template_dir / "ROLE.md"
        if dept_soul.exists():
            try:
                raw = dept_soul.read_text(encoding="utf-8")
                # 去掉 YAML frontmatter，避免与新 frontmatter 冲突
                if raw.startswith("---"):
                    try:
                        end = raw.index("---", 3)
                        raw = raw[end + 3 :].strip()
                    except ValueError:
                        pass
                dept_soul_text = raw.strip()
            except OSError:
                pass
        if dept_role.exists():
            try:
                raw = dept_role.read_text(encoding="utf-8")
                if raw.startswith("---"):
                    try:
                        end = raw.index("---", 3)
                        raw = raw[end + 3 :].strip()
                    except ValueError:
                        pass
                dept_role_text = raw.strip()
            except OSError:
                pass

    soul_content = (
        f"---\n"
        f"name: {name}\n"
        f"department: {department}\n"
        + (f"title: {title_norm}\n" if title_norm else "")
        + f"---\n\n"
        f"# {name}\n\n"
        f"你是 **{name}**，隶属 **{dept_label}**{title_clause}。"
        f"你的行事风格与能力借鉴本部门一贯传统，但你拥有独立的人格与判断。\n"
    )
    if dept_soul_text:
        soul_content += (
            "\n## 风格指引（参考本部门传统）\n\n"
            "> 以下为本部门通用风格描述，仅作参考——你在此基础上保留个性，"
            "**不要把下文中的部门主官身份套用到自己身上**。\n\n"
            f"{dept_soul_text}\n"
        )

    role_content = (
        f"# {name} — 职责\n\n"
        f"作为 {dept_label} 的 {title_norm or '官员'}，你负责执行交办的任务，"
        f"并参考本部门职责描述行事。\n"
    )
    if dept_role_text:
        role_content += f"\n## 部门职责参考\n\n{dept_role_text}\n"

    if overwrite or not soul_path.exists():
        soul_path.write_text(soul_content, encoding="utf-8")
    if overwrite or not role_path.exists():
        role_path.write_text(role_content, encoding="utf-8")

    return soul_path, role_path


@personas_router.get("/personas/jingcha")
def persona_jingcha(request: Request):
    """京察·官员考核(迭代 7):全体官员绩效考语 + 汇总。"""
    from tianshu.persona.jingcha import Jingcha

    storage: Storage = request.app.state.storage
    return ApiResponse(success=True, data=Jingcha(storage).review())


@personas_router.get("/personas")
def list_personas(request: Request):
    selector: OfficialSelector = request.app.state.official_selector
    storage: Storage = request.app.state.storage
    personas = selector.list_all()
    # Build department name lookup
    departments = storage.list_departments()
    dept_name_map = {d["id"]: d["name"] for d in departments}
    return ApiResponse(
        success=True,
        data=[
            {
                "id": p.id,
                "name": p.name,
                "department": p.department,
                "department_name": dept_name_map.get(p.department),
                "title": p.title,
                "tools_allowed": p.tools_allowed,
                "tools_denied": p.tools_denied,
                "allowed_paths": p.allowed_paths,
                "skills_allowed": p.skills_allowed,
                "tool_tier_max": p.tool_tier_max,
                "can_delegate": p.can_delegate,
                "memory_global_read": p.memory_global_read,
                "delegates_to": p.delegates_to,
                "llm_config_name": p.llm_config_name,
            }
            for p in personas
        ],
    )


@personas_router.post("/personas/import/preview", response_model=ApiResponse)
async def preview_persona_import(request: Request):
    """从 openclaw/hermes 读配置作**导入预览**(只读,不落库):供预填百官创建表单。

    一次性 seed(非 sync):只取人格+能力(SOUL/职责/技能/模型),排除记忆/学习/渠道。
    """
    from tianshu.persona.import_source import (
        PersonaImportError,
        detect_default_path,
        import_from,
    )

    body = await request.json()
    source = body.get("source")
    if source not in ("openclaw", "hermes"):
        raise HTTPException(status_code=400, detail="source must be 'openclaw' or 'hermes'")
    path_str = (body.get("path") or "").strip()
    path = Path(path_str).expanduser() if path_str else detect_default_path(source)
    if path is None:
        raise HTTPException(
            status_code=400,
            detail=f"未提供路径,且未探测到默认 {source} 配置目录",
        )
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不存在或非目录: {path}")
    try:
        draft = import_from(source, path)
    except PersonaImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ApiResponse(
        success=True,
        data={
            "source": draft.source,
            "soul_body": draft.soul_body,
            "role_body": draft.role_body,
            "suggested_name": draft.suggested_name,
            "suggested_model": draft.suggested_model,
            "skills": [
                {"name": s.name, "source_dir": s.source_dir, "description": s.description}
                for s in draft.skills
            ],
            "source_notes": draft.source_notes,
        },
    )


@personas_router.post("/personas", response_model=ApiResponse, status_code=201)
async def create_persona(request: Request):
    from tianshu.persona.loader import PersonaLoader
    from tianshu.persona.model import AgentPersona

    loader: PersonaLoader = request.app.state.persona_loader
    body = await request.json()
    if not body.get("id") or not body.get("name") or not body.get("department"):
        raise HTTPException(status_code=400, detail="id, name, department are required")
    if body.get("import_skill_paths"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Direct Skill installation through Persona import is unavailable; "
                "review detected Skills separately through the governed candidate flow"
            ),
        )

    # Check duplicate
    if loader.get(body["id"]):
        raise HTTPException(status_code=409, detail=f"Persona '{body['id']}' already exists")

    # Validate department exists
    storage: Storage = request.app.state.storage
    dept = storage.get_department(body["department"])
    if not dept:
        raise HTTPException(
            status_code=400, detail=f"Department '{body['department']}' does not exist"
        )

    # Validate llm_config_name FK if provided
    llm_config_name = body.get("llm_config_name") or None
    if llm_config_name:
        config_manager: ConfigManager = request.app.state.config_manager
        if not config_manager.get_config(llm_config_name):
            raise HTTPException(
                status_code=400,
                detail=f"LLM config '{llm_config_name}' does not exist",
            )

    # 校验 title（可选；若提供，长度限制以避免长文本注入到 prompt）
    title = body.get("title")
    if title is not None:
        title = str(title).strip() or None
    if title and len(title) > _TITLE_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"title 过长（最多 {_TITLE_MAX_LEN} 字）",
        )

    # Runtime identity: 选了角色模板就用模板内容生成 SOUL.md/ROLE.md；
    # 否则渲染个性化骨架（部门 SOUL/ROLE 作为风格指引附在末尾，避免错认部门主官）。
    runtime_personas_dir: Path = request.app.state.runtime_personas_dir
    runtime_dir = runtime_personas_dir / body["id"]
    template_id = body.get("template_id") or None
    imported_soul = body.get("imported_soul")
    if imported_soul is not None and not template_id:
        # 从外部(openclaw/hermes)导入作种子:注天枢身份 frontmatter 到导入的 SOUL 正文,
        # 职责取导入的 ROLE 正文(空则默认)。一次性种子,已存在不覆盖(与模板路径同语义)。
        runtime_dir.mkdir(parents=True, exist_ok=True)
        soul_path = runtime_dir / "SOUL.md"
        role_path = runtime_dir / "ROLE.md"
        fm = f"---\nname: {body['name']}\ndepartment: {body['department']}\n"
        if title:
            fm += f"title: {title}\n"
        fm += "---\n"
        if not soul_path.exists():
            soul_path.write_text(fm + "\n" + str(imported_soul).strip() + "\n", encoding="utf-8")
        if not role_path.exists():
            role_body = str(body.get("imported_role") or "").strip() or f"# {body['name']} — 职责\n"
            role_path.write_text(role_body + "\n", encoding="utf-8")
    elif template_id:
        template_lang = body.get("template_lang") or "zh"
        if template_lang not in ("zh", "en"):
            raise HTTPException(
                status_code=400,
                detail="template_lang must be 'zh' or 'en'",
            )
        library: TemplateLibrary = request.app.state.template_library
        template = library.get(template_lang, template_id)
        if not template:
            raise HTTPException(
                status_code=400,
                detail=f"Template '{template_id}' ({template_lang}) not found",
            )
        soul_md, role_md = library.render(
            template,
            name=body["name"],
            department=body["department"],
            title=title,
        )
        runtime_dir.mkdir(parents=True, exist_ok=True)
        soul_path = runtime_dir / "SOUL.md"
        role_path = runtime_dir / "ROLE.md"
        if not soul_path.exists():
            soul_path.write_text(soul_md, encoding="utf-8")
        if not role_path.exists():
            role_path.write_text(role_md, encoding="utf-8")
    else:
        template_dir = loader._dir / body["department"]
        soul_path, role_path = _render_persona_identity_files(
            runtime_dir=runtime_dir,
            persona_id=body["id"],
            name=body["name"],
            department=body["department"],
            title=title,
            dept_label=dept.get("name") or body["department"],
            department_template_dir=template_dir if template_dir.is_dir() else None,
            overwrite=False,
        )

    memory_manager = request.app.state.memory_manager
    memory_path = memory_manager.memory_dir / body["id"] / "MEMORY.md"

    skills_allowed = list(body.get("skills_allowed", []))

    persona = AgentPersona(
        id=body["id"],
        name=body["name"],
        department=body["department"],
        title=title,
        soul_path=soul_path,
        role_path=role_path,
        memory_path=memory_path,
        tools_allowed=body.get("tools_allowed", []),
        allowed_paths=body.get("allowed_paths", []),
        tools_denied=body.get("tools_denied", []),
        skills_allowed=skills_allowed,
        tool_tier_max=body.get("tool_tier_max", 0),
        can_delegate=body.get("can_delegate", False),
        memory_global_read=body.get("memory_global_read", False),
        delegates_to=body.get("delegates_to", []),
        llm_config_name=llm_config_name,
    )
    loader.save(persona)
    return ApiResponse(
        success=True,
        data={
            "id": persona.id,
            "name": persona.name,
            "department": persona.department,
            "title": persona.title,
            "tools_allowed": persona.tools_allowed,
            "tools_denied": persona.tools_denied,
            "allowed_paths": persona.allowed_paths,
            "skills_allowed": persona.skills_allowed,
            "tool_tier_max": persona.tool_tier_max,
            "can_delegate": persona.can_delegate,
            "memory_global_read": persona.memory_global_read,
            "delegates_to": persona.delegates_to,
            "llm_config_name": persona.llm_config_name,
        },
    )


@personas_router.get("/persona-templates")
def list_persona_templates(
    request: Request,
    lang: str = Query(default="zh"),
):
    """List vendored role templates for a language, grouped by category."""
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="lang must be 'zh' or 'en'")
    library: TemplateLibrary = request.app.state.template_library
    grouped: dict[str, list[dict]] = {}
    for t in library.list(lang):
        grouped.setdefault(t.category, []).append(
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "emoji": t.emoji,
            }
        )
    data = [
        {"category": category, "templates": templates}
        for category, templates in sorted(grouped.items())
    ]
    return ApiResponse(success=True, data=data)


@personas_router.get("/persona-templates/{template_id}")
def get_persona_template(
    template_id: str,
    request: Request,
    lang: str = Query(default="zh"),
):
    """Return a template's SOUL/ROLE preview (rendered with placeholder name)."""
    if lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="lang must be 'zh' or 'en'")
    library: TemplateLibrary = request.app.state.template_library
    template = library.get(lang, template_id)
    if not template:
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_id}' ({lang}) not found",
        )
    soul_md, role_md = library.render(
        template,
        name=template.name,
        department="",
        title=None,
    )
    return ApiResponse(
        success=True,
        data={
            "id": template.id,
            "lang": template.lang,
            "category": template.category,
            "name": template.name,
            "description": template.description,
            "emoji": template.emoji,
            "soul_preview": soul_md,
            "role_preview": role_md,
        },
    )


@personas_router.put("/personas/{persona_id}", response_model=ApiResponse)
async def update_persona(persona_id: str, request: Request):
    from tianshu.persona.loader import PersonaLoader

    loader: PersonaLoader = request.app.state.persona_loader
    storage: Storage = request.app.state.storage

    existing = loader.get(persona_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")

    body = await request.json()
    # Validate department FK if changing department
    if "department" in body and not storage.get_department(body["department"]):
        raise HTTPException(
            status_code=400, detail=f"Department '{body['department']}' does not exist"
        )
    # Validate llm_config_name FK if provided
    if "llm_config_name" in body and body["llm_config_name"]:
        config_manager: ConfigManager = request.app.state.config_manager
        if not config_manager.get_config(body["llm_config_name"]):
            raise HTTPException(
                status_code=400,
                detail=f"LLM config '{body['llm_config_name']}' does not exist",
            )
    # Normalize & validate title
    if "title" in body:
        t = body["title"]
        if t is not None:
            t = str(t).strip() or None
        if t and len(t) > _TITLE_MAX_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"title 过长（最多 {_TITLE_MAX_LEN} 字）",
            )
        body["title"] = t
    storage.update_persona(persona_id, **body)
    # Reload from DB to refresh in-memory cache
    loader.load_all()

    updated = loader.get(persona_id)
    return ApiResponse(
        success=True,
        data={
            "id": updated.id,
            "name": updated.name,
            "department": updated.department,
            "title": updated.title,
            "tools_allowed": updated.tools_allowed,
            "tools_denied": updated.tools_denied,
            "allowed_paths": updated.allowed_paths,
            "skills_allowed": updated.skills_allowed,
            "tool_tier_max": updated.tool_tier_max,
            "can_delegate": updated.can_delegate,
            "memory_global_read": updated.memory_global_read,
            "delegates_to": updated.delegates_to,
            "llm_config_name": updated.llm_config_name,
        },
    )


@personas_router.post(
    "/personas/{persona_id}/regenerate-identity",
    response_model=ApiResponse,
)
def regenerate_persona_identity(persona_id: str, request: Request):
    """基于当前 name/department/title 重新生成 SOUL.md/ROLE.md（覆盖现有文件）。

    用于：
    - 老的用户自建官员（SOUL.md 还是从部门模板拷过来的）想刷新为个性化身份。
    - 修改 title 之后想让文件层也同步。
    """
    from tianshu.persona.loader import PersonaLoader

    loader: PersonaLoader = request.app.state.persona_loader
    storage: Storage = request.app.state.storage

    persona = loader.get(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")

    dept = storage.get_department(persona.department)
    dept_label = dept.get("name") if dept else persona.department

    runtime_personas_dir: Path = request.app.state.runtime_personas_dir
    runtime_dir = runtime_personas_dir / persona_id
    template_dir = loader._dir / persona.department
    soul_path, role_path = _render_persona_identity_files(
        runtime_dir=runtime_dir,
        persona_id=persona_id,
        name=persona.name,
        department=persona.department,
        title=persona.title,
        dept_label=dept_label,
        department_template_dir=template_dir if template_dir.is_dir() else None,
        overwrite=True,
    )
    return ApiResponse(
        success=True,
        data={
            "id": persona_id,
            "soul_path": str(soul_path),
            "role_path": str(role_path),
        },
    )


@personas_router.delete("/personas/{persona_id}", response_model=ApiResponse)
def delete_persona(persona_id: str, request: Request):
    from tianshu.persona.loader import PersonaLoader

    loader: PersonaLoader = request.app.state.persona_loader
    existing = loader.get(persona_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")

    loader.delete(persona_id)
    return ApiResponse(success=True, data={"id": persona_id})


@personas_router.get("/personas/{persona_id}/metrics")
def get_persona_metrics(persona_id: str, request: Request):
    evaluator: PerformanceEvaluator = request.app.state.evaluator
    metrics = evaluator.evaluate(persona_id)
    return ApiResponse(success=True, data=metrics.model_dump())


@personas_router.get("/personas/{persona_id}/profile")
def get_persona_profile(persona_id: str, request: Request):
    from tianshu.persona.profile_schema import parse_profile

    persona_loader = request.app.state.persona_loader
    if not persona_loader.get(persona_id):
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    runtime_personas_dir: Path = request.app.state.runtime_personas_dir
    profile_path = runtime_personas_dir / persona_id / "PROFILE.md"
    if not profile_path.exists():
        return ApiResponse(
            success=True,
            data={
                "persona_id": persona_id,
                "exists": False,
                "frontmatter": None,
                "markdown": "",
                "history": [],
            },
        )
    text = profile_path.read_text(encoding="utf-8")
    fm, auto, manual = parse_profile(text)
    history_dir = profile_path.parent / "profile_history"
    history: list[dict] = []
    if history_dir.exists():
        for f in sorted(history_dir.iterdir(), reverse=True):
            if f.is_file() and f.suffix == ".md":
                history.append({"name": f.name, "mtime": f.stat().st_mtime})
    return ApiResponse(
        success=True,
        data={
            "persona_id": persona_id,
            "exists": True,
            "frontmatter": fm.__dict__ if fm else None,
            "markdown": text,
            "auto_section": auto,
            "manual_section": manual,
            "history": history,
        },
    )


@personas_router.post("/personas/{persona_id}/synthesize")
async def trigger_profile_synthesis(persona_id: str, request: Request):
    persona_loader = request.app.state.persona_loader
    if not persona_loader.get(persona_id):
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    syn = request.app.state.profile_synthesizer
    event_bus = request.app.state.event_bus

    from fastapi.responses import StreamingResponse

    async def event_stream():
        import asyncio
        import json

        queue: asyncio.Queue = asyncio.Queue()

        async def _listener(ev):
            pid = ev.payload.get("persona_id")
            if pid == persona_id:
                await queue.put(ev)

        for et in [
            "profile.synthesis.started",
            "profile.synthesis.completed",
            "profile.synthesis.degraded",
            "profile.synthesis.failed",
            "profile.synthesis.skipped",
        ]:
            event_bus.on_local(
                et,
                _listener,
                priority=10,
            )

        task = asyncio.create_task(syn.run(persona_id, trigger_source="api_manual"))
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=60)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    if task.done():
                        break
                    continue
                data = {
                    "event": ev.event_type,
                    "payload": ev.payload,
                }
                yield f"event: {ev.event_type}\ndata: {json.dumps(data)}\n\n"
                if ev.event_type in (
                    "profile.synthesis.completed",
                    "profile.synthesis.degraded",
                    "profile.synthesis.failed",
                    "profile.synthesis.skipped",
                ):
                    break
        finally:
            for et in [
                "profile.synthesis.started",
                "profile.synthesis.completed",
                "profile.synthesis.degraded",
                "profile.synthesis.failed",
                "profile.synthesis.skipped",
            ]:
                event_bus.off_local(et, _listener)
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class _ProfileManualUpdate(BaseModel):
    manual_section: str


@personas_router.put("/personas/{persona_id}/profile/manual", response_model=ApiResponse)
def update_profile_manual(persona_id: str, body: _ProfileManualUpdate, request: Request):
    persona_loader = request.app.state.persona_loader
    if not persona_loader.get(persona_id):
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    from tianshu.persona.profile_io import atomic_write
    from tianshu.persona.profile_renderer import render_markdown
    from tianshu.persona.profile_schema import parse_profile

    runtime_personas_dir: Path = request.app.state.runtime_personas_dir
    path = runtime_personas_dir / persona_id / "PROFILE.md"
    if not path.exists():
        raise HTTPException(404, "Profile not found; synthesize first")
    text = path.read_text(encoding="utf-8")
    fm, auto, _ = parse_profile(text)
    if not fm:
        raise HTTPException(500, "Corrupted frontmatter")
    fm.manually_edited = bool(body.manual_section.strip())
    new_md = render_markdown(fm, auto, body.manual_section)
    atomic_write(path, new_md)
    return ApiResponse(success=True, data={"persona_id": persona_id, "manual_updated": True})


@personas_router.get("/personas/{persona_id}/profile/history/{version}")
def get_profile_history(persona_id: str, version: int, request: Request):
    persona_loader = request.app.state.persona_loader
    if not persona_loader.get(persona_id):
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    runtime_personas_dir: Path = request.app.state.runtime_personas_dir
    history_dir = runtime_personas_dir / persona_id / "profile_history"
    if not history_dir.exists():
        raise HTTPException(404, "No history")
    for f in history_dir.iterdir():
        if f.name.startswith(f"v{version}-") and f.suffix == ".md":
            return ApiResponse(
                success=True,
                data={
                    "persona_id": persona_id,
                    "version": version,
                    "name": f.name,
                    "markdown": f.read_text(encoding="utf-8"),
                },
            )
    raise HTTPException(404, f"Version v{version} not found")


# --- Official routing rules (吏部·铨选) ---


@personas_router.get("/routing/rules")
def get_routing_rules(request: Request):
    """Get official routing rules and keyword mappings."""
    selector: OfficialSelector = request.app.state.official_selector
    personas = selector.list_all()
    delegation = []
    for p in personas:
        if p.can_delegate and p.delegates_to:
            delegation.append(
                {
                    "from_id": p.id,
                    "from_name": p.name,
                    "delegates_to": p.delegates_to,
                }
            )
    return ApiResponse(
        success=True,
        data={
            "default_map": selector.get_default_map(),
            "keyword_map": selector.get_keyword_map(),
            "delegation_chains": delegation,
        },
    )
