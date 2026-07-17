"""技能与工具路由（藏兵阁）：skills / tools。无统一 prefix，路径写全。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from tianshu.gateway.auth import get_auth_context
from tianshu.models import ApiResponse
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import CandidateSourceChannel
from tianshu.skills.install_service import (
    ProposedSkillMemberV1,
    ProposeSkillCommand,
    SkillInstallService,
)
from tianshu.skills.installer import (
    SkillPackageRenderError,
    SkillPackageSnapshotError,
    render_skill_document_body,
    snapshot_skill_package,
)
from tianshu.storage import Storage

skills_router = APIRouter(tags=["skills"])


@skills_router.post("/skills/curate")
async def curate_skills(
    request: Request,
    dry_run: bool = Query(default=True),
):
    """Manually trigger the skill curator (修撰). dry_run=true previews without writing."""
    curator = getattr(request.app.state, "skill_curator", None)
    if curator is None:
        raise HTTPException(status_code=503, detail="skill curator not available")
    result = await curator.run(trigger_source="manual", dry_run=dry_run)
    return ApiResponse(success=True, data=result.to_dict())


# --- Skills endpoints (藏兵阁) ---


@skills_router.get("/skills")
def list_skills(request: Request):
    from tianshu.skills.loader import SkillsLoader

    loader: SkillsLoader = request.app.state.skills_loader
    metrics = getattr(request.app.state, "skill_metrics_store", None)
    # 用 dict(s) 拷贝，避免污染 loader 内部可能缓存的 metadata dict
    skills = [dict(s) for s in loader.list_all_metadata()]
    if metrics is not None:
        for s in skills:
            m = metrics.get(s.get("name", ""))
            if m:
                s.update(
                    {
                        "created_by": m.created_by,
                        "state": m.state,
                        "pinned": m.pinned,
                        "human_curated": m.human_curated,
                        "usage_count": m.usage_count,
                        "success_rate": m.success_rate,
                        "created_at": m.created_at,
                    }
                )
    return ApiResponse(success=True, data=skills)


@skills_router.get("/skills/{name}")
def get_skill(name: str, request: Request):
    from tianshu.skills.loader import SkillsLoader

    loader: SkillsLoader = request.app.state.skills_loader
    skill = loader.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return ApiResponse(success=True, data=skill)


@skills_router.put("/skills/{name}", response_model=ApiResponse)
async def update_skill(name: str, request: Request):
    """Human edit: save SKILL.md content + mark as golden (human_curated)."""
    from tianshu.skills.loader import SkillsLoader

    loader: SkillsLoader = request.app.state.skills_loader
    body = await request.json()
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="content is required")
    current = loader.get_skill(name)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found") from None
    try:
        snapshot = snapshot_skill_package(Path(str(current.get("path", ""))), expected_name=name)
    except SkillPackageSnapshotError:
        raise HTTPException(status_code=409, detail="skill_package_snapshot_invalid") from None
    base_members = tuple(
        ProposedSkillMemberV1(path=member.path, kind=member.kind, content=member.content)
        for member in snapshot
    )
    raw_document = next(
        (member.content for member in snapshot if member.path == "SKILL.md"),
        None,
    )
    if raw_document is None:
        raise HTTPException(status_code=409, detail="skill_package_render_invalid")
    try:
        candidate_document = render_skill_document_body(
            raw_document,
            str(content),
            expected_name=name,
        )
    except SkillPackageRenderError:
        raise HTTPException(status_code=409, detail="skill_package_render_invalid") from None
    service: SkillInstallService = request.app.state.skill_install_service
    auth = get_auth_context(request)
    candidate = service.propose(
        _inline_command(
            name=name,
            base_members=base_members,
            content=candidate_document,
            source_channel=CandidateSourceChannel.API,
            correlation_id=auth.correlation_id,
        ),
        auth=auth,
    )
    return ApiResponse(
        success=True,
        data={"candidate_id": candidate.candidate_id, "lifecycle": candidate.lifecycle},
    )


@skills_router.post("/skills/{name}/archive")
def archive_skill(name: str, request: Request):
    """Human undo: archive an agent-created skill (recoverable)."""
    raise HTTPException(status_code=409, detail="governed_skill_service_required")


@skills_router.post("/skills/{name}/pin")
async def pin_skill(name: str, request: Request):
    """Pin/unpin: exempt from curator transitions."""
    metrics = getattr(request.app.state, "skill_metrics_store", None)
    if metrics is None:
        raise HTTPException(status_code=503, detail="metrics store unavailable")
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    pinned = bool(body.get("pinned", True))
    metrics.ensure_exists(name)
    metrics.set_pinned(name, pinned)
    metrics.touch_human_action(name)
    return ApiResponse(success=True, data={"name": name, "pinned": pinned})


@skills_router.post("/skills", response_model=ApiResponse, status_code=201)
async def create_skill(request: Request):
    body = await request.json()
    name = body.get("name")
    content = body.get("content", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    from tianshu.skills.loader import SkillsLoader

    loader: SkillsLoader = request.app.state.skills_loader
    try:
        current = loader.get_skill(str(name))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_skill_name") from None
    if current is not None:
        raise HTTPException(status_code=409, detail="skill_already_exists")
    service: SkillInstallService = request.app.state.skill_install_service
    auth = get_auth_context(request)
    candidate = service.propose(
        _inline_command(
            name=str(name),
            base_members=None,
            content=str(content),
            source_channel=CandidateSourceChannel.API,
            correlation_id=auth.correlation_id,
        ),
        auth=auth,
    )
    return ApiResponse(
        success=True,
        data={"candidate_id": candidate.candidate_id, "lifecycle": candidate.lifecycle},
    )


@skills_router.post("/skills/candidates/{candidate_id}/stage", response_model=ApiResponse)
def stage_skill_candidate(candidate_id: str, request: Request):
    service: SkillInstallService = request.app.state.skill_install_service
    staged = service.stage(candidate_id, auth=get_auth_context(request))
    return ApiResponse(
        success=True,
        data={
            "candidate_id": staged.candidate_id,
            "lifecycle": staged.lifecycle,
            "live_changed": False,
        },
    )


@skills_router.delete("/skills/{name}", response_model=ApiResponse)
def delete_skill(name: str, request: Request):
    from tianshu.skills.loader import SkillsLoader

    loader: SkillsLoader = request.app.state.skills_loader
    # Check if it's a builtin skill
    skill = loader.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    if skill["source"] == "builtin":
        raise HTTPException(status_code=403, detail="Cannot delete builtin skills")
    raise HTTPException(status_code=409, detail="governed_skill_service_required")


def _inline_command(
    *,
    name: str,
    base_members: tuple[ProposedSkillMemberV1, ...] | None,
    content: str,
    source_channel: CandidateSourceChannel,
    correlation_id: str,
) -> ProposeSkillCommand:
    identity = hashlib.sha256(f"{correlation_id}:{name}:{content}".encode()).hexdigest()
    candidate_members = [ProposedSkillMemberV1(path="SKILL.md", kind="file", content=content)]
    if base_members is not None:
        candidate_members.extend(member for member in base_members if member.path != "SKILL.md")
    base_digest = (
        None
        if base_members is None
        else canonical_sha256(
            {"members": [member.model_dump(mode="json") for member in base_members]}
        )
    )
    return ProposeSkillCommand(
        command_id=f"skill-api-{identity}",
        name=name,
        version=f"candidate-{identity[:16]}",
        base_version=("absent" if base_digest is None else f"base-{base_digest[:16]}"),
        base_state="absent" if base_members is None else "present",
        source_channel=source_channel,
        base_members=(() if base_members is None else base_members),
        members=tuple(candidate_members),
        evidence_bundle_ids=(),
        restore_point_ref=(
            f"skill:{name}:absent" if base_members is None else f"skill:{name}:current"
        ),
    )


# --- Tools endpoints (藏兵阁) ---


@skills_router.get("/tools")
def list_tools(request: Request):
    from tianshu.tools.registry import ToolRegistry

    registry: ToolRegistry = request.app.state.tool_registry
    persona_loader = request.app.state.persona_loader
    definitions = registry.list_definitions()
    disabled = registry.list_disabled()

    # Build tool -> personas mapping
    # 用 persona/match.py 的通配符匹配，让 SOUL.md 可以写 "mcp_github_*"
    # 一行授权整个 MCP server。
    from tianshu.persona.match import persona_can_use

    all_personas = list(persona_loader._personas.values())
    result = []
    for defn in definitions:
        personas = [p.id for p in all_personas if persona_can_use(p, defn.name, defn.tier)]
        result.append(
            {
                "name": defn.name,
                "description": defn.description,
                "tier": defn.tier,
                "parameters": defn.parameters,
                "personas": personas,
                "enabled": defn.name not in disabled,
            }
        )
    return ApiResponse(success=True, data=result)


class _ToolEnabledPatch(BaseModel):
    enabled: bool


@skills_router.patch("/tools/{tool_name}")
def update_tool_enabled(tool_name: str, body: _ToolEnabledPatch, request: Request):
    from tianshu.tools.registry import ToolRegistry

    registry: ToolRegistry = request.app.state.tool_registry
    # 未注册直接 404
    defn = registry.get_definition(tool_name)
    if defn is None:
        raise HTTPException(404, f"tool '{tool_name}' not registered")

    storage: Storage = request.app.state.storage
    storage.set_tool_enabled(tool_name, body.enabled)
    if body.enabled:
        registry.enable(tool_name)
    else:
        registry.disable(tool_name)
    return ApiResponse(success=True, data={"name": tool_name, "enabled": body.enabled})
