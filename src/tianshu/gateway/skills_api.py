"""技能与工具路由（藏兵阁）：skills / tools。无统一 prefix，路径写全。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tianshu.gateway.auth import get_auth_context
from tianshu.models import ApiResponse
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import CandidateSourceChannel
from tianshu.skills.install_service import (
    EvaluateSkillGateCommand,
    ProposedSkillMemberV1,
    ProposeSkillCommand,
    SkillEvidenceInvalid,
    SkillEvidenceNotFound,
    SkillInstallService,
)
from tianshu.skills.installer import (
    SkillPackageRenderError,
    SkillPackageSnapshotError,
    render_skill_document_body,
    snapshot_skill_package,
)
from tianshu.storage import Storage
from tianshu.storage.evolution_repo import EvolutionRepositoryConflict

skills_router = APIRouter(tags=["skills"])


class _CreateSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    content: str = ""
    evidence_bundle_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_bundle_ids")
    @classmethod
    def validate_evidence_bundle_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("evidence_bundle_ids must be unique non-blank values")
        return values


class _EvaluateSkillGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_version: int = Field(ge=1)
    evidence_bundle_ids: list[str] = Field(min_length=1)

    @field_validator("evidence_bundle_ids")
    @classmethod
    def validate_evidence_bundle_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("evidence_bundle_ids must be unique non-blank values")
        return values


def _raise_skill_evidence_error(exc: Exception) -> NoReturn:
    if isinstance(exc, SkillEvidenceNotFound):
        raise HTTPException(404, {"code": "evidence_bundle_not_found"}) from exc
    if isinstance(exc, (SkillEvidenceInvalid, EvolutionRepositoryConflict)):
        raise HTTPException(409, {"code": str(exc)}) from exc
    raise exc


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
def create_skill(body: _CreateSkillRequest, request: Request):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    from tianshu.skills.loader import SkillsLoader

    loader: SkillsLoader = request.app.state.skills_loader
    try:
        current = loader.get_skill(body.name)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_skill_name") from None
    if current is not None:
        raise HTTPException(status_code=409, detail="skill_already_exists")
    service: SkillInstallService = request.app.state.skill_install_service
    auth = get_auth_context(request)
    try:
        candidate = service.propose(
            _inline_command(
                name=body.name,
                base_members=None,
                content=body.content,
                source_channel=CandidateSourceChannel.API,
                correlation_id=auth.correlation_id,
                evidence_bundle_ids=tuple(body.evidence_bundle_ids),
            ),
            auth=auth,
        )
    except (SkillEvidenceInvalid, SkillEvidenceNotFound) as exc:
        _raise_skill_evidence_error(exc)
    return ApiResponse(
        success=True,
        data={"candidate_id": candidate.candidate_id, "lifecycle": candidate.lifecycle},
    )


@skills_router.post(
    "/skills/candidates/{candidate_id}/gate/evaluate",
    response_model=ApiResponse,
)
def evaluate_skill_candidate_gate(
    candidate_id: str,
    body: _EvaluateSkillGateRequest,
    request: Request,
):
    service: SkillInstallService = request.app.state.skill_install_service
    try:
        report = service.evaluate_gate(
            candidate_id,
            EvaluateSkillGateCommand(
                expected_version=body.expected_version,
                evidence_bundle_ids=tuple(body.evidence_bundle_ids),
            ),
            auth=get_auth_context(request),
        )
    except (SkillEvidenceInvalid, SkillEvidenceNotFound, EvolutionRepositoryConflict) as exc:
        _raise_skill_evidence_error(exc)
    return ApiResponse(success=True, data=report.model_dump(mode="json"))


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
    evidence_bundle_ids: tuple[str, ...] = (),
) -> ProposeSkillCommand:
    evidence_identity = ",".join(evidence_bundle_ids)
    identity = hashlib.sha256(
        f"{correlation_id}:{name}:{content}:{evidence_identity}".encode()
    ).hexdigest()
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
        evidence_bundle_ids=evidence_bundle_ids,
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
