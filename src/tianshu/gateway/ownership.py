"""Gateway helpers for task resource ownership checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from tianshu.authz import can_access_submitter, has_global_task_access
from tianshu.gateway.auth import get_auth_context
from tianshu.models import Edict, Memorial
from tianshu.models.dag import DAGExecution
from tianshu.models.principal import AuthContext

if TYPE_CHECKING:
    from tianshu.storage import Storage


def _context(request: Request, context: AuthContext | None) -> AuthContext:
    return context if context is not None else get_auth_context(request)


def require_owned_edict(
    request: Request,
    edict_id: str,
    *,
    context: AuthContext | None = None,
    not_found_detail: object | None = None,
) -> Edict:
    """Load an Edict only when the authenticated principal may access it."""

    storage: Storage = request.app.state.storage
    edict = storage.get_edict(edict_id)
    if edict is None or not can_access_submitter(_context(request, context), edict.submitter):
        raise HTTPException(
            status_code=404,
            detail=(
                not_found_detail
                if not_found_detail is not None
                else f"Edict '{edict_id}' not found"
            ),
        )
    return edict


def require_owned_memorial(
    request: Request,
    memorial_id: str,
    *,
    context: AuthContext | None = None,
) -> Memorial:
    """Load a Memorial only through an Edict visible to the principal."""

    storage: Storage = request.app.state.storage
    memorial = storage.get_memorial(memorial_id)
    auth = _context(request, context)
    if memorial is None:
        raise HTTPException(status_code=404, detail=f"Memorial '{memorial_id}' not found")
    edict = storage.get_edict(memorial.edict_id)
    if edict is None or not can_access_submitter(auth, edict.submitter):
        raise HTTPException(status_code=404, detail=f"Memorial '{memorial_id}' not found")
    return memorial


def require_owned_scheduler_job(
    request: Request,
    job_id: str,
    *,
    context: AuthContext | None = None,
) -> dict:
    """Load a scheduler job without disclosing another principal's job ID."""

    storage: Storage = request.app.state.storage
    row = storage.get_scheduler_job(job_id)
    auth = _context(request, context)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Scheduler job '{job_id}' not found")
    if has_global_task_access(auth):
        return row
    edict = storage.get_edict(str(row["edict_id"]))
    if edict is None or not can_access_submitter(auth, edict.submitter):
        raise HTTPException(status_code=404, detail=f"Scheduler job '{job_id}' not found")
    return row


def require_owned_dag(
    request: Request,
    dag_id: str,
    *,
    context: AuthContext | None = None,
) -> DAGExecution:
    """Load a DAG only through an Edict visible to the principal."""

    storage: Storage = request.app.state.storage
    dag = storage.get_dag_execution(dag_id)
    auth = _context(request, context)
    if dag is None:
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")
    edict = storage.get_edict(dag.edict_id)
    if edict is None or not can_access_submitter(auth, edict.submitter):
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")
    return dag


__all__ = [
    "require_owned_dag",
    "require_owned_edict",
    "require_owned_memorial",
    "require_owned_scheduler_job",
]
