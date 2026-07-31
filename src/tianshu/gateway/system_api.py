"""系统运维路由（system-prompt + event-bus + hooks + notifications）：提示词文件管理/预览/分层、事件总线与钩子自省、通知渠道列表。无统一 prefix，路径写全。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.bus.event_bus import EventBus
from tianshu.models import ApiResponse
from tianshu.resources.overlay import (
    court_override_path,
    reset_court_override,
    resolve_court_read,
)
from tianshu.storage import Storage

system_router = APIRouter(tags=["system"])


# --- System Prompt endpoints (藏兵阁) ---


_PROMPT_FILE_WHITELIST = {"SOUL.md", "ROLE.md", "COURT.md", "MEMORY.md"}


def _read_dept_display_name(persona_dir) -> str:
    """Read department display name from SOUL.md or COURT.md frontmatter."""
    import yaml

    # Try SOUL.md first, then COURT.md (for court directory)
    for fname in ("SOUL.md", "COURT.md"):
        md_path = persona_dir / fname
        if not md_path.exists():
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.index("---", 3)
                meta = yaml.safe_load(text[3:end]) or {}
                raw = meta.get("name", "")
                if not raw:
                    continue
                # Use the Chinese portion before the English parenthetical
                if "(" in raw:
                    raw = raw[: raw.index("(")].strip()
                # Map well-known English names to Chinese
                if raw == "Imperial Court":
                    return "朝廷"
                return raw
        except Exception:
            continue
    return persona_dir.name


def _resolve_runtime_identity_seed(request: Request, persona_id: str) -> None:
    """Ensure runtime SOUL.md / ROLE.md exist for a persona (seeded from template)."""
    persona_loader = request.app.state.persona_loader
    personas_dir = request.app.state.personas_dir
    persona = persona_loader.get(persona_id)
    if persona:
        template_dir = personas_dir / (persona.department or persona.id)
        if not template_dir.is_dir():
            template_dir = personas_dir / persona.id
    else:
        template_dir = personas_dir / persona_id
    persona_loader.ensure_runtime_identity(persona_id, template_dir)


def _prompt_file_path(request: Request, persona_id: str, filename: str):
    """Resolve the writable backing path for a prompt file.

    Runtime-backed (per-persona, evolvable):
      - SOUL.md / ROLE.md  → runtime_personas_dir/{pid}/
      - MEMORY.md          → memory_dir/{pid}/

    Overlay-backed (packaged default is immutable):
      - COURT.md           → runtime_personas_dir/{pid}/COURT.md
        (reads fall back to the packaged default when no override exists)
    """
    runtime_personas_dir = request.app.state.runtime_personas_dir
    if filename == "MEMORY.md":
        memory_manager = request.app.state.memory_manager
        return memory_manager.memory_dir / persona_id / filename
    return runtime_personas_dir / persona_id / filename


@system_router.get("/system-prompt/files")
def list_prompt_files(request: Request):
    from datetime import UTC, datetime

    personas_dir: Path = request.app.state.personas_dir
    memory_manager = request.app.state.memory_manager
    memory_dir: Path = memory_manager.memory_dir
    runtime_personas_dir: Path = request.app.state.runtime_personas_dir
    result = []
    departments: dict[str, str] = {}
    if not personas_dir.is_dir():
        return ApiResponse(success=True, data={"files": result, "departments": departments})
    for persona_dir in sorted(personas_dir.iterdir()):
        if not persona_dir.is_dir():
            continue
        persona_id = persona_dir.name
        departments[persona_id] = _read_dept_display_name(persona_dir)
        seen_files: set[str] = set()
        for md_file in sorted(persona_dir.glob("*.md")):
            if md_file.name not in _PROMPT_FILE_WHITELIST:
                continue
            if md_file.name == "MEMORY.md":
                runtime = memory_dir / persona_id / "MEMORY.md"
                target = runtime if runtime.is_file() else md_file
            else:
                # SOUL/ROLE/COURT: data overlay wins, packaged default fallback
                runtime = runtime_personas_dir / persona_id / md_file.name
                target = runtime if runtime.is_file() else md_file
            stat = target.stat()
            seen_files.add(md_file.name)
            result.append(
                {
                    "persona_id": persona_id,
                    "filename": md_file.name,
                    "path": str(target),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                }
            )
        # Surface runtime-only files for personas without a template entry
        runtime_memory = memory_dir / persona_id / "MEMORY.md"
        if "MEMORY.md" not in seen_files and runtime_memory.is_file():
            stat = runtime_memory.stat()
            result.append(
                {
                    "persona_id": persona_id,
                    "filename": "MEMORY.md",
                    "path": str(runtime_memory),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                }
            )
    return ApiResponse(success=True, data={"files": result, "departments": departments})


def _require_court_persona(persona_id: str) -> None:
    """COURT.md 是全局共享层，override 路径固定为 court/COURT.md；
    其他 persona_id 的写入会产生永远不被读取的孤儿 override，直接拒绝。"""
    if persona_id != "court":
        raise HTTPException(
            status_code=400,
            detail="COURT.md is a shared court-level file; use persona_id 'court'",
        )


@system_router.get("/system-prompt/files/{persona_id}/{filename}")
def get_prompt_file(persona_id: str, filename: str, request: Request):
    if filename not in _PROMPT_FILE_WHITELIST:
        raise HTTPException(status_code=400, detail=f"File '{filename}' is not in whitelist")
    if filename == "COURT.md":
        _require_court_persona(persona_id)
        # overlay override 优先，packaged 默认回退（单一事实源 helper）
        file_path = resolve_court_read(request.app.state.runtime_personas_dir)
    else:
        if filename in ("SOUL.md", "ROLE.md"):
            # Lazy seed so reading a persona that has never been loaded still works
            _resolve_runtime_identity_seed(request, persona_id)
        file_path = _prompt_file_path(request, persona_id, filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {persona_id}/{filename}")
    content = file_path.read_text(encoding="utf-8")
    return ApiResponse(
        success=True, data={"persona_id": persona_id, "filename": filename, "content": content}
    )


@system_router.put("/system-prompt/files/{persona_id}/{filename}", response_model=ApiResponse)
async def update_prompt_file(persona_id: str, filename: str, request: Request):
    if filename not in _PROMPT_FILE_WHITELIST:
        raise HTTPException(status_code=400, detail=f"File '{filename}' is not in whitelist")
    body = await request.json()
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="content is required")
    if filename == "MEMORY.md":
        memory_manager = request.app.state.memory_manager
        memory_manager.md_backend.write_core_memory(persona_id, content)
        return ApiResponse(
            success=True,
            data={"persona_id": persona_id, "filename": filename, "size": len(content)},
        )
    if filename == "COURT.md":
        _require_court_persona(persona_id)
        file_path = court_override_path(request.app.state.runtime_personas_dir)
    else:
        if filename in ("SOUL.md", "ROLE.md"):
            _resolve_runtime_identity_seed(request, persona_id)
        file_path = _prompt_file_path(request, persona_id, filename)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return ApiResponse(
        success=True, data={"persona_id": persona_id, "filename": filename, "size": len(content)}
    )


@system_router.post(
    "/system-prompt/files/{persona_id}/{filename}/reset", response_model=ApiResponse
)
def reset_prompt_file(persona_id: str, filename: str, request: Request):
    """Reset a prompt file.

    - SOUL.md / ROLE.md / MEMORY.md: copy the department template into the
      runtime file (existing semantics).
    - COURT.md: frozen G1.5 semantics — delete the data-overlay override so
      reads fall back to the current packaged default. Idempotent; never
      copies packaged bytes into the overlay.
    """
    if filename == "COURT.md":
        _require_court_persona(persona_id)
        reset_court_override(request.app.state.runtime_personas_dir)
        return ApiResponse(
            success=True,
            data={"persona_id": persona_id, "filename": filename, "reset": "overlay-removed"},
        )
    if filename not in ("SOUL.md", "ROLE.md", "MEMORY.md"):
        raise HTTPException(
            status_code=400, detail=f"File '{filename}' cannot be reset (not runtime-backed)"
        )
    persona_loader = request.app.state.persona_loader
    personas_dir = request.app.state.personas_dir
    persona = persona_loader.get(persona_id)
    template_dir = personas_dir / (persona.department if persona else persona_id)
    if not template_dir.is_dir():
        template_dir = personas_dir / persona_id
    template_file = template_dir / filename
    if not template_file.is_file():
        raise HTTPException(status_code=404, detail=f"Template not found: {template_file}")
    content = template_file.read_text(encoding="utf-8")
    if filename == "MEMORY.md":
        request.app.state.memory_manager.md_backend.write_core_memory(persona_id, content)
    else:
        target = _prompt_file_path(request, persona_id, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return ApiResponse(
        success=True, data={"persona_id": persona_id, "filename": filename, "size": len(content)}
    )


def _resolve_persona(persona_loader, persona_id: str):
    """Resolve persona from DB, falling back to file-system template directory."""
    persona = persona_loader.get(persona_id)
    if persona:
        return persona
    # Fallback: load on-the-fly from file-system template directory
    persona_dir = persona_loader._dir / persona_id
    if persona_dir.is_dir():
        return persona_loader._load_persona_from_dir(persona_dir)
    return None


@system_router.get("/system-prompt/preview/{persona_id}")
async def preview_system_prompt(persona_id: str, request: Request):
    from tianshu.persona.prompt_builder import PromptBuilder

    prompt_builder: PromptBuilder = request.app.state.prompt_builder
    persona_loader = request.app.state.persona_loader
    persona = _resolve_persona(persona_loader, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")
    # Build a dummy edict for preview
    from tianshu.models.edict import Edict

    dummy_edict = Edict(title="Preview", goal="System prompt preview")
    preview = await prompt_builder.build(dummy_edict, persona=persona)
    return ApiResponse(success=True, data={"persona_id": persona_id, "prompt": preview})


# --- EventBus introspection endpoints (运维监控台) ---


@system_router.get("/event-bus/handlers")
def list_event_bus_handlers(request: Request):
    """List all registered event handlers with priorities."""
    event_bus: EventBus = request.app.state.event_bus
    result = {}
    for event_type, entries in event_bus._handlers.items():
        result[event_type] = [
            {
                "handler": getattr(e.handler, "__qualname__", type(e.handler).__qualname__),
                "priority": e.priority,
            }
            for e in entries
        ]
    return ApiResponse(success=True, data=result)


@system_router.get("/event-bus/stats")
def get_event_bus_stats(request: Request):
    """Get event type distribution from storage."""
    storage: Storage = request.app.state.storage
    stats = storage.get_event_stats()
    return ApiResponse(success=True, data=stats)


@system_router.get("/event-bus/recent")
def get_recent_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get recent events across all edicts."""
    storage: Storage = request.app.state.storage
    events = storage.get_recent_events(limit=limit)
    return ApiResponse(success=True, data=events)


# --- Hooks introspection endpoints (运维监控台) ---


@system_router.get("/hooks/registry")
def list_hooks_registry(request: Request):
    """List all registered hooks with handler info and priorities."""
    from tianshu.kernel.hooks import HookRegistry

    hook_registry: HookRegistry = request.app.state.hook_registry
    result = {}
    for hook_type, entries in hook_registry._hooks.items():
        result[hook_type.value] = [
            {"handler": e.handler.__qualname__, "priority": e.priority} for e in entries
        ]
    return ApiResponse(success=True, data=result)


# --- Notification channel endpoints (通政司·驿传) ---


@system_router.get("/notifications/channels")
def list_notification_channels(request: Request):
    """List registered notification channels with rate limit info."""
    from tianshu.notifier.channel_registry import ChannelRegistry

    registry: ChannelRegistry = request.app.state.channel_registry
    channels = []
    for name in registry.list_channels():
        channel = registry.get(name)
        rpm = registry._rate_limits.get(name, 10)
        recent_sends = len(registry._send_log.get(name, []))
        channels.append(
            {
                "name": name,
                "type": type(channel).__name__,
                "rpm_limit": rpm,
                "recent_sends": recent_sends,
            }
        )
    return ApiResponse(success=True, data=channels)


# --- Prompt layer visualization (翰林院·拟旨) ---


@system_router.get("/system-prompt/layers/{persona_id}")
async def get_prompt_layers(persona_id: str, request: Request):
    """Get system prompt breakdown by layer."""
    from tianshu.persona.prompt_builder import PromptBuilder

    prompt_builder: PromptBuilder = request.app.state.prompt_builder
    persona_loader = request.app.state.persona_loader
    persona = _resolve_persona(persona_loader, persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{persona_id}' not found")
    from tianshu.models.edict import Edict

    dummy_edict = Edict(title="Preview", goal="Layer analysis")
    layers = await prompt_builder.build_layers(dummy_edict, persona=persona)
    return ApiResponse(success=True, data=layers)
