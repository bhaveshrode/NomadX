from fastapi import APIRouter, HTTPException, Depends, status

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId

router = APIRouter(prefix="/api/financial-consistency", tags=["financial-consistency"])


@router.get("/check", status_code=status.HTTP_200_OK)
async def check_financial_consistency(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Runs cross-context financial invariant checks on-demand for the current organization.
    """
    async with container.financial_consistency_query_service() as query_service:
        report = await query_service.check_financial_consistency(
            OrganizationId(ctx.organization_id)
        )
        return report


@router.get("/metrics", status_code=status.HTTP_200_OK)
async def get_consistency_metrics(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Retrieves consistency metrics (health scores, active violations counts) for the current organization.
    """
    async with container.financial_consistency_query_service() as query_service:
        metrics = await query_service.get_consistency_metrics(
            OrganizationId(ctx.organization_id)
        )
        return metrics


@router.get("/health", status_code=status.HTTP_200_OK)
async def get_financial_health(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Retrieves high-level cross-context financial processing health.
    """
    async with container.financial_consistency_query_service() as query_service:
        report = await query_service.check_financial_consistency(
            OrganizationId(ctx.organization_id)
        )
        return {
            "status": report["status"],
            "checked_at": report["checked_at"],
            "active_violations_count": report["summary"]["total_violations"]
        }


@router.get("/violations", status_code=status.HTTP_200_OK)
async def get_active_violations(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Lists all currently active financial consistency violations for the current organization.
    """
    async with container.financial_consistency_query_service() as query_service:
        violations = await query_service.get_active_violations(
            OrganizationId(ctx.organization_id)
        )
        return violations


@router.get("/history", status_code=status.HTTP_200_OK)
async def get_violation_history(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Lists the complete historical log of all consistency findings for the current organization.
    """
    async with container.financial_consistency_query_service() as query_service:
        history = await query_service.get_violation_history(
            OrganizationId(ctx.organization_id)
        )
        return history


@router.get("/timeline/{entity_id}", status_code=status.HTTP_200_OK)
async def get_entity_timeline(
    entity_id: str,
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Generates a consolidated chronological timeline for a given financial entity.
    """
    async with container.financial_consistency_query_service() as query_service:
        timeline = await query_service.get_entity_timeline(
            OrganizationId(ctx.organization_id),
            entity_id
        )
        return timeline


@router.post("/automation/run", status_code=status.HTTP_200_OK)
async def run_integrity_sweep(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Triggers a system-wide financial integrity sweep across all organizations.
    Only users with ADMIN or OWNER role can trigger this system-wide run.
    """
    if ctx.role not in ("SYSTEM", "PLATFORM_ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation authorized only for platform administrator or system roles."
        )
    async with container.financial_consistency_automation_worker() as worker:
        res = await worker.run_integrity_sweep()
        return res
