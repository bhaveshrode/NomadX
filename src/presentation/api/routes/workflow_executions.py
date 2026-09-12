import json
from typing import List, Optional, Dict, Any
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Depends, Query, Path, status
from sqlalchemy import select

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.workflow.value_objects import WorkflowId, WorkflowVersion
from ....domain.workflow.execution import WorkflowExecution, WorkflowExecutionId
from ....application.workflow.event_processor import WorkflowEventProcessor
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.repositories import PostgresWorkflowExecutionRepository, PostgresWorkflowRepository
from ....infrastructure.database.models import OutboxEventDbModel, WorkflowVersionDbModel, WorkflowDbModel, WorkflowExecutionDbModel

router = APIRouter(tags=["workflow-executions"])


@router.get("/api/workflow-executions")
async def list_executions(
    status_filter: Optional[str] = Query(None, alias="status"),
    workflow_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Lists executions for the organization with pagination and filters."""
    async with container.workflow_query_service() as query_service:
        executions = await query_service.list_executions(
            organization_id=OrganizationId(ctx.organization_id),
            status=status_filter,
            workflow_id=workflow_id,
            limit=limit,
            offset=offset
        )
        return executions


@router.get("/api/workflow-executions/{id}")
async def get_execution(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Retrieves detailed fields of a specific workflow execution."""
    async with container.workflow_query_service() as query_service:
        execution = await query_service.get_execution(
            organization_id=OrganizationId(ctx.organization_id),
            execution_id=id
        )
        if not execution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow execution not found."
            )
        return execution


@router.post("/api/workflow-executions/{id}/retry")
async def retry_execution(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Manually triggers a retry attempt on a FAILED or DEAD_LETTERED execution."""
    if ctx.role not in ("ADMIN", "OWNER", "SYSTEM"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to retry execution."
        )

    async with session_manager.session() as session:
        execution_repo = PostgresWorkflowExecutionRepository(session)
        # Check tenant context: query workflow first to match org_id
        exec_domain = await execution_repo.get_by_id(WorkflowExecutionId(id))
        if not exec_domain:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow execution not found."
            )

        wf_repo = PostgresWorkflowRepository(session)
        wf = await wf_repo.get_by_id(exec_domain.workflow_id)
        if not wf or wf.organization_id.value != ctx.organization_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow execution not found."
            )

        if exec_domain.status not in ("FAILED", "DEAD_LETTERED", "RETRYING"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Execution in status {exec_domain.status} cannot be retried."
            )

        # Transition execution back to RETRYING status, scheduled immediately
        exec_domain._props["status"] = "RETRYING"
        exec_domain._props["next_retry_at"] = None  # Reset next_retry_at to execute immediately
        await execution_repo.save(exec_domain)
        await session.commit()

    # Trigger processor to run retries immediately
    processor = WorkflowEventProcessor()
    await processor.process_events()

    # Fetch updated state
    async with container.workflow_query_service() as query_service:
        execution_dto = await query_service.get_execution(
            organization_id=OrganizationId(ctx.organization_id),
            execution_id=id
        )
        return execution_dto


@router.post("/api/workflow-executions/{id}/replay")
async def replay_execution(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """Replays execution context forming a brand new execution attempt."""
    if ctx.role not in ("ADMIN", "OWNER", "SYSTEM"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to replay execution."
        )

    async with session_manager.session() as session:
        execution_repo = PostgresWorkflowExecutionRepository(session)
        exec_domain = await execution_repo.get_by_id(WorkflowExecutionId(id))
        if not exec_domain:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow execution not found."
            )

        wf_repo = PostgresWorkflowRepository(session)
        wf = await wf_repo.get_by_id(exec_domain.workflow_id)
        if not wf or wf.organization_id.value != ctx.organization_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow execution not found."
            )

        # Fetch original version details
        stmt_ver = select(WorkflowVersionDbModel).where(
            WorkflowVersionDbModel.workflow_id == exec_domain.workflow_id.value,
            WorkflowVersionDbModel.version == exec_domain.version.value
        )
        res_ver = await session.execute(stmt_ver)
        ver_model = res_ver.scalar_one_or_none()
        if not ver_model:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow version definition details not found."
            )

        # Fetch original outbox event details
        stmt_event = select(OutboxEventDbModel).where(OutboxEventDbModel.id == exec_domain.source_event_id)
        res_event = await session.execute(stmt_event)
        orig_event = res_event.scalar_one_or_none()

        # To support multiple replays bypasses constraint, we suffix event id
        replay_event_id = f"{exec_domain.source_event_id}-replay-{uuid4().hex[:8]}"

        # Insert a cloned outbox event in database representing replay event payload
        cloned_payload = orig_event.payload if orig_event else "{}"
        cloned_event = OutboxEventDbModel(
            id=replay_event_id,
            event_type=exec_domain.source_event_type,
            payload=cloned_payload,
            processed=False
        )
        session.add(cloned_event)

        # Replay event registration in execution log
        exec_domain.replay()
        await execution_repo.save(exec_domain)

        await session.commit()

    # Trigger processor to run the pipeline for the replayed event immediately
    processor = WorkflowEventProcessor()
    await processor.process_events()

    # Fetch the newly generated execution attempt by querying its source_event_id
    async with session_manager.session() as session:
        stmt_new = select(WorkflowExecutionDbModel).where(
            WorkflowExecutionDbModel.source_event_id == replay_event_id
        )
        res_new = await session.execute(stmt_new)
        new_exec_model = res_new.scalar_one_or_none()
        if not new_exec_model:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Replay execution failed to start."
            )
        
        # Map and return it
        async with container.workflow_query_service() as query_service:
            new_execution_dto = await query_service.get_execution(
                organization_id=OrganizationId(ctx.organization_id),
                execution_id=new_exec_model.id
            )
            return new_execution_dto
