from fastapi import APIRouter, HTTPException, Depends, status

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext, require_roles
from ....domain.organization.value_objects import OrganizationId

router = APIRouter(prefix="/api/financial-recovery", tags=["financial-recovery"])


@router.post("/recheck", status_code=status.HTTP_200_OK)
async def recheck_consistency(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Reruns cross-context consistency checks on-demand and resolves corrected violations.
    """
    async with container.financial_recovery_service() as recovery_service:
        report = await recovery_service.recheck_consistency(
            OrganizationId(ctx.organization_id),
            ctx
        )
        return report


@router.post("/retry-event/{event_id}", status_code=status.HTTP_200_OK)
async def retry_failed_event(
    event_id: str,
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Retries processing a failed/dead-lettered outbox event within organization context.
    """
    async with container.financial_recovery_service() as recovery_service:
        res = await recovery_service.retry_failed_event(
            OrganizationId(ctx.organization_id),
            event_id,
            ctx
        )
    if res.get("status") == "NOT_FOUND":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=res.get("error_message") or "Event not found."
        )
    if res.get("status") == "FAILED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("error_message")
        )
    return res


@router.post("/replay-event/{event_id}", status_code=status.HTTP_200_OK)
async def replay_event(
    event_id: str,
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Replays a previously processed outbox event safely.
    """
    async with container.financial_recovery_service() as recovery_service:
        res = await recovery_service.replay_event(
            OrganizationId(ctx.organization_id),
            event_id,
            ctx
        )
    if res.get("status") == "NOT_FOUND":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=res.get("error_message") or "Event not found."
        )
    if res.get("status") in ("FAILED", "ESCALATED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("error_message")
        )
    return res


@router.post("/recover-stuck", status_code=status.HTTP_200_OK)
async def recover_stuck_processing(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Safely identifies and clears stuck events or reconciliation batches.
    """
    async with container.financial_recovery_service() as recovery_service:
        res = await recovery_service.recover_stuck_processing(
            OrganizationId(ctx.organization_id),
            ctx
        )
        return res


@router.get("/remediations", status_code=status.HTTP_200_OK)
async def get_remediation_history(
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Retrieves the complete audit trail of recovery actions for the current organization.
    """
    async with container.financial_recovery_service() as recovery_service:
        history = await recovery_service.get_remediation_history(
            OrganizationId(ctx.organization_id)
        )
        return history

