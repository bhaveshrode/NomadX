from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, status, Depends, Path

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId

router = APIRouter(tags=["notification-reports"])


@router.get("/api/notifications/summary", response_model=Dict[str, Any])
async def get_notification_summary(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Returns breakdown of notifications by status for the active tenant organization.
    """
    # Enforce tenant context organization
    org_id = OrganizationId(ctx.organization_id)
    
    async with container.notification_query_service() as qs:
        summary = await qs.get_notification_summary(org_id)
        return summary


@router.get("/api/notifications/delivery-stats", response_model=Dict[str, Any])
async def get_delivery_stats(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Returns counts breakdown of delivered/failed per channel for the active tenant organization.
    """
    org_id = OrganizationId(ctx.organization_id)
    
    async with container.notification_query_service() as qs:
        stats = await qs.get_delivery_stats(org_id)
        return stats


@router.get("/api/notifications/{id}/attempts", response_model=List[Dict[str, Any]])
async def get_delivery_attempts(
    id: str = Path(..., description="Notification ID"),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Returns a chronological list of delivery attempts for a given notification ID.
    Enforces tenant isolation.
    """
    org_id = OrganizationId(ctx.organization_id)
    
    async with container.notification_query_service() as qs:
        attempts = await qs.get_delivery_attempts(org_id, id)
        return attempts


@router.get("/api/notifications/failed", response_model=List[Dict[str, Any]])
async def get_failed_notifications(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Returns list of failed notifications for the active tenant organization.
    """
    org_id = OrganizationId(ctx.organization_id)
    async with container.notification_query_service() as qs:
        return await qs.get_failed_notifications(org_id)


@router.get("/api/notifications/dead-letter", response_model=List[Dict[str, Any]])
async def get_dead_lettered_notifications(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Returns list of dead-lettered notifications for the active tenant organization.
    """
    org_id = OrganizationId(ctx.organization_id)
    async with container.notification_query_service() as qs:
        return await qs.get_dead_lettered_notifications(org_id)


@router.get("/api/notifications/providers/health", response_model=List[Dict[str, Any]])
async def get_provider_health_states(
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Returns global provider health states.
    """
    async with container.notification_query_service() as qs:
        return await qs.get_provider_health_states()
