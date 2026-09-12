import json
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Header, Query, Path, status, Response
from pydantic import BaseModel
from sqlalchemy import select

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.workflow.value_objects import WorkflowId, WorkflowIdempotencyKey
from ....domain.shared.clock.clock import Clock
from ....domain.shared.result.result import ResultError
from ....application.workflow.commands import (
    CreateWorkflowCommand,
    CreateWorkflowVersionCommand,
    ActivateWorkflowCommand,
    PauseWorkflowCommand,
    ArchiveWorkflowCommand,
    ActivateWorkflowVersionCommand
)
from ....application.workflow.query_service import IWorkflowQueryService
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.models import WorkflowDbModel, WorkflowVersionDbModel

router = APIRouter(tags=["workflows"])


# --- API DTOs ---

class CreateWorkflowRequest(BaseModel):
    reference: str
    name: str
    description: Optional[str] = None
    trigger_event_type: Optional[str] = None
    conditions_data: Optional[dict] = None
    actions_data: Optional[List[dict]] = None
    idempotency_key: Optional[str] = None


class CreateWorkflowVersionRequest(BaseModel):
    trigger_event_type: str
    conditions_data: dict
    actions_data: List[dict]


# --- Error Helper ---

def map_error_to_http(error: ResultError):
    if error.code == "WORKFLOW_NOT_FOUND":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message)
    elif error.code == "UNAUTHORIZED_ORGANIZATION_ACCESS":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found.")
    elif error.code in (
        "DUPLICATE_WORKFLOW",
        "DUPLICATE_WORKFLOW_REFERENCE",
        "DUPLICATE_WORKFLOW_IDEMPOTENCY",
        "INVALID_WORKFLOW_STATE",
        "WORKFLOW_ALREADY_ACTIVE",
        "WORKFLOW_ALREADY_ARCHIVED"
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message)
    elif error.code in ("INVALID_WORKFLOW_DATA", "INVALID_WORKFLOW_VERSION"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error.message)
    elif error.code == "WORKFLOW_VERSION_NOT_FOUND":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error.message)


# --- Role Guards ---

def require_roles(allowed_roles: List[str]):
    async def dependency(ctx: ExecutionContext = Depends(get_execution_context)) -> ExecutionContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not authorized for this member role."
            )
        return ctx
    return dependency


# --- Definition Validation Helper ---

def validate_workflow_definition(ver_model: Any) -> None:
    if not ver_model:
        raise HTTPException(status_code=400, detail="Workflow version definition details not found.")
    
    # 1. Trigger validation
    trigger = json.loads(ver_model.trigger) if isinstance(ver_model.trigger, str) else ver_model.trigger
    if not trigger or not trigger.get("event_type"):
        raise HTTPException(status_code=400, detail="Workflow trigger definition is invalid: missing event_type.")

    # 2. Actions validation
    actions = json.loads(ver_model.actions) if isinstance(ver_model.actions, str) else ver_model.actions
    if not actions:
        raise HTTPException(status_code=400, detail="Workflow has no configured actions.")

    valid_types = {"SEND_NOTIFICATION", "CREATE_SETTLEMENT"}
    for idx, action in enumerate(actions):
        action_type = action.get("action_type")
        if not action_type or action_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"Unsupported action type '{action_type}' at index {idx}.")
        
        parameters = action.get("parameters") or {}
        
        # Check variables brackets syntax (unbalanced curly braces)
        for k, val in parameters.items():
            if isinstance(val, str):
                open_cnt = val.count("{{")
                close_cnt = val.count("}}")
                if open_cnt != close_cnt:
                    raise HTTPException(status_code=400, detail=f"Invalid templating syntax in parameter '{k}' (unbalanced curly braces).")


# --- Endpoints ---

@router.post("/api/workflows", response_model=dict)
async def create_workflow(
    payload: CreateWorkflowRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Creates a new workflow in DRAFT status, supports tenant-scoped idempotency."""
    idem_key = idempotency_key or payload.idempotency_key
    if not idem_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required in header or body.")

    # Pre-check idempotency key to return 200 OK cleanly
    async with container.workflow_repository() as repo:
        existing = await repo.find_by_idempotency_key(
            OrganizationId(ctx.organization_id),
            WorkflowIdempotencyKey(idem_key)
        )
        if existing:
            response.status_code = status.HTTP_200_OK
            return {
                "id": existing.id.value,
                "organization_id": existing.organization_id.value,
                "reference": existing.reference.value,
                "name": existing.name.value,
                "description": existing.description,
                "status": existing.status.value,
                "active_version_number": existing.active_version_number,
                "created_at": existing.created_at.isoformat(),
                "updated_at": existing.updated_at.isoformat()
            }

    cmd = CreateWorkflowCommand(
        organization_id=ctx.organization_id,
        reference=payload.reference,
        name=payload.name,
        description=payload.description,
        idempotency_key=idem_key,
        trigger_event_type=payload.trigger_event_type,
        conditions_data=payload.conditions_data,
        actions_data=payload.actions_data
    )

    async with container.create_workflow_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)

        workflow = res.value
        response.status_code = status.HTTP_201_CREATED

        # Return serialized metadata
        return {
            "id": workflow.id.value,
            "organization_id": workflow.organization_id.value,
            "reference": workflow.reference.value,
            "name": workflow.name.value,
            "description": workflow.description,
            "status": workflow.status.value,
            "active_version_number": workflow.active_version_number,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat()
        }


@router.get("/api/workflows", response_model=List[dict])
async def list_workflows(
    status: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    reference: Optional[str] = Query(None),
    trigger: Optional[str] = Query(None),
    active_version: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Lists organization workflows with optional query filters."""
    async with container.workflow_query_service() as q_service:
        workflows = await q_service.list_workflows(
            organization_id=OrganizationId(ctx.organization_id),
            status=status,
            name=name,
            reference=reference,
            trigger=trigger,
            active_version=active_version,
            limit=limit,
            offset=offset
        )
        return workflows


@router.get("/api/workflows/{workflow_id}", response_model=dict)
async def get_workflow(
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves details of a single workflow in the caller's organization."""
    async with container.workflow_query_service() as q_service:
        wf = await q_service.get_workflow(OrganizationId(ctx.organization_id), workflow_id)
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return wf


@router.get("/api/workflows/{workflow_id}/versions", response_model=List[dict])
async def list_workflow_versions(
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Lists all definition versions configured for a workflow."""
    async with container.workflow_query_service() as q_service:
        versions = await q_service.list_versions(OrganizationId(ctx.organization_id), workflow_id)
        if not versions:
            # Check if workflow exists at all to return 404
            wf = await q_service.get_workflow(OrganizationId(ctx.organization_id), workflow_id)
            if not wf:
                raise HTTPException(status_code=404, detail="Workflow not found.")
        return versions


@router.get("/api/workflows/{workflow_id}/versions/{version}", response_model=dict)
async def get_workflow_version(
    workflow_id: str = Path(..., min_length=1),
    version: int = Path(..., ge=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Retrieves details of a specific workflow definition version."""
    async with container.workflow_query_service() as q_service:
        ver = await q_service.get_version(OrganizationId(ctx.organization_id), workflow_id, version)
        if not ver:
            raise HTTPException(status_code=404, detail="Workflow version not found.")
        return ver


@router.post("/api/workflows/{workflow_id}/versions", status_code=status.HTTP_201_CREATED, response_model=dict)
async def create_workflow_version(
    payload: CreateWorkflowVersionRequest,
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Creates a new definition version for the workflow."""
    cmd = CreateWorkflowVersionCommand(
        workflow_id=workflow_id,
        organization_id=ctx.organization_id,
        trigger_event_type=payload.trigger_event_type,
        conditions_data=payload.conditions_data,
        actions_data=payload.actions_data
    )

    async with container.create_workflow_version_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)

        version = res.value
        return {
            "workflow_id": workflow_id,
            "version": version.value
        }


@router.post("/api/workflows/{workflow_id}/activate", status_code=status.HTTP_200_OK)
async def activate_workflow(
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Activates a DRAFT or PAUSED workflow, performing definition validation checks first."""
    async with session_manager.session() as session:
        stmt_wf = select(WorkflowDbModel).where(
            WorkflowDbModel.organization_id == ctx.organization_id,
            WorkflowDbModel.id == workflow_id
        )
        res_wf = await session.execute(stmt_wf)
        wf_db = res_wf.scalar_one_or_none()
        if not wf_db:
            raise HTTPException(status_code=404, detail="Workflow not found.")

        # Load active/current version details for validation
        ver_num = wf_db.active_version_number
        if ver_num is None:
            stmt_max = select(WorkflowVersionDbModel.version).where(
                WorkflowVersionDbModel.workflow_id == workflow_id
            ).order_by(WorkflowVersionDbModel.version.desc()).limit(1)
            res_max = await session.execute(stmt_max)
            ver_num = res_max.scalar_one_or_none() or 1
        stmt_ver = select(WorkflowVersionDbModel).where(
            WorkflowVersionDbModel.workflow_id == workflow_id,
            WorkflowVersionDbModel.version == ver_num
        )
        res_ver = await session.execute(stmt_ver)
        ver_db = res_ver.scalar_one_or_none()
        validate_workflow_definition(ver_db)

    cmd = ActivateWorkflowCommand(workflow_id=workflow_id, organization_id=ctx.organization_id)
    async with container.activate_workflow_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return {"status": "success", "message": "Workflow activated."}


@router.post("/api/workflows/{workflow_id}/pause", status_code=status.HTTP_200_OK)
async def pause_workflow(
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Pauses an ACTIVE workflow."""
    cmd = PauseWorkflowCommand(workflow_id=workflow_id, organization_id=ctx.organization_id)
    async with container.pause_workflow_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return {"status": "success", "message": "Workflow paused."}


@router.post("/api/workflows/{workflow_id}/archive", status_code=status.HTTP_200_OK)
async def archive_workflow(
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Permanently archives a workflow."""
    cmd = ArchiveWorkflowCommand(workflow_id=workflow_id, organization_id=ctx.organization_id)
    async with container.archive_workflow_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return {"status": "success", "message": "Workflow archived."}


@router.post("/api/workflows/{workflow_id}/versions/{version}/activate", status_code=status.HTTP_200_OK)
async def activate_workflow_version(
    workflow_id: str = Path(..., min_length=1),
    version: int = Path(..., ge=1),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """Activates a specific definition version for execution, running definition validation check."""
    async with session_manager.session() as session:
        # Pre-validate definition version before letting it be activated
        stmt_ver = select(WorkflowVersionDbModel).where(
            WorkflowVersionDbModel.workflow_id == workflow_id,
            WorkflowVersionDbModel.version == version
        )
        res_ver = await session.execute(stmt_ver)
        ver_db = res_ver.scalar_one_or_none()
        if not ver_db:
            raise HTTPException(status_code=404, detail="Workflow version definition details not found.")
        validate_workflow_definition(ver_db)

    cmd = ActivateWorkflowVersionCommand(
        workflow_id=workflow_id,
        organization_id=ctx.organization_id,
        version_number=version
    )
    async with container.activate_workflow_version_handler() as handler:
        res = await handler.handle(cmd, ctx)
        if res.is_failure:
            map_error_to_http(res.error)
        return {"status": "success", "message": f"Workflow version {version} set as active."}


@router.get("/api/workflows/{workflow_id}/metrics", response_model=dict)
async def get_workflow_metrics(
    workflow_id: str = Path(..., min_length=1),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """Calculates operational and aggregate execution metrics for a workflow."""
    async with container.workflow_query_service() as q_service:
        metrics = await q_service.get_workflow_metrics(
            organization_id=OrganizationId(ctx.organization_id),
            workflow_id=workflow_id
        )
        if not metrics:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return metrics
