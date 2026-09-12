from datetime import datetime
from typing import List, Optional
from uuid import uuid4
from fastapi import APIRouter, HTTPException, status, Depends, Path, Query, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ....bootstrap.container import container
from ....presentation.api.dependencies.context import get_execution_context, ExecutionContext
from ....domain.organization.value_objects import OrganizationId
from ....domain.notification.value_objects import NotificationId, NotificationIdempotencyKey
from ....domain.notification.notification import Notification, NotificationStatus
from ....infrastructure.database.session import session_manager
from ....infrastructure.database.repositories import PostgresNotificationRepository
from ....application.notification.commands import (
    CreateNotificationCommand,
    StartNotificationProcessingCommand,
    MarkNotificationDeliveredCommand,
    MarkNotificationFailedCommand,
    RetryNotificationCommand,
    CancelNotificationCommand
)
from ....application.notification.replay_commands import ReplayNotificationCommand
from ....domain.shared.result.result import Result

router = APIRouter(tags=["notifications"])


# --- Schemas ---

class CreateNotificationRequest(BaseModel):
    notification_type: str = Field(..., min_length=1)
    channel: str = Field(..., min_length=1)
    recipient: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    template_id: str = Field(..., min_length=1)
    payload: Optional[dict] = None
    priority: str = Field("NORMAL")
    reference: Optional[str] = None
    idempotency_key: Optional[str] = None
    source_event_id: Optional[str] = None
    source_event_type: Optional[str] = None


# --- Helpers & DTO Mappers ---

def _notification_to_dto(n: Notification) -> dict:
    failure_dto = None
    if n.failure:
        failure_dto = {
            "code": n.failure.code.value,
            "message": n.failure.message,
            "retryable": n.failure.retryable
        }

    return {
        "id": n.id.value,
        "organization_id": n.organization_id.value,
        "reference": n.reference.value,
        "notification_type": n.notification_type.value,
        "channel": n.channel.value,
        "recipient": n.recipient.destination,
        "subject": n.subject,
        "template_id": n.template_id.value,
        "payload": dict(n.payload.data),
        "priority": n.priority.value,
        "status": n.status.value,
        "idempotency_key": n.idempotency_key.value,
        "source_event_id": n.source_event_id,
        "source_event_type": n.source_event_type,
        "failure": failure_dto,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
        "delivered_at": n.delivered_at.isoformat() if n.delivered_at else None,
        "processing_started_at": n.processing_started_at.isoformat() if n.processing_started_at else None
    }


def _handle_result(result: Result, success_status: int = status.HTTP_200_OK):
    if result.is_failure:
        err = result.error
        if err.code in ("NOTIFICATION_NOT_FOUND", "NOT_FOUND"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code in ("UNAUTHORIZED_ORGANIZATION_ACCESS", "UNAUTHORIZED_ACCESS"):
            # Prevent leakage, return 404
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
        elif err.code in ("DUPLICATE_NOTIFICATION", "INVALID_NOTIFICATION_STATE", "NOTIFICATION_NOT_RETRYABLE", "NOTIFICATION_ALREADY_PROCESSED"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code == "DUPLICATE_NOTIFICATION_IDEMPOTENCY":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        elif err.code in ("VALIDATION_ERROR", "INVALID_NOTIFICATION_DATA"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err.message)
    return result.value


# --- Authorization Dependency ---

def require_roles(allowed_roles: List[str]):
    async def dependency(ctx: ExecutionContext = Depends(get_execution_context)) -> ExecutionContext:
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not authorized for this member role."
            )
        return ctx
    return dependency


# --- Endpoints ---

@router.post("/api/notifications", status_code=status.HTTP_201_CREATED)
async def create_notification(
    payload: CreateNotificationRequest,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Creates a new notification instruction.
    Supports HTTP Idempotency via header or payload idempotency key.
    """
    idem = idempotency_key_header or payload.idempotency_key or str(uuid4())

    # Pre-check idempotency key to return 200 OK if already exists
    async with session_manager.session() as session:
        repo = PostgresNotificationRepository(session)
        existing = await repo.find_by_idempotency_key(
            OrganizationId(ctx.organization_id),
            NotificationIdempotencyKey(idem)
        )
        if existing:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=_notification_to_dto(existing)
            )

        # Pre-check source event to return 200 OK if already exists
        if payload.source_event_id and payload.source_event_type:
            existing_events = await repo.find_by_source_event(
                payload.source_event_id,
                payload.source_event_type
            )
            for existing_ev in existing_events:
                if existing_ev.organization_id.value == ctx.organization_id:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content=_notification_to_dto(existing_ev)
                    )

    notification_id = str(uuid4())
    ref = payload.reference or f"NOT-{uuid4().hex[:12].upper()}"
    command = CreateNotificationCommand(
        notification_id=notification_id,
        organization_id=ctx.organization_id,
        reference=ref,
        notification_type=payload.notification_type,
        recipient_destination=payload.recipient,
        channel=payload.channel,
        subject=payload.subject,
        template_id=payload.template_id,
        payload=payload.payload or {},
        priority=payload.priority,
        idempotency_key=idem,
        source_event_id=payload.source_event_id or "",
        source_event_type=payload.source_event_type or ""
    )

    async with container.create_notification_handler() as handler:
        result = await handler.handle(command, ctx)

    # Double check for concurrent race condition where DB uniqueness constraint throws DUPLICATE_NOTIFICATION_IDEMPOTENCY
    if result.is_failure and result.error.code == "DUPLICATE_NOTIFICATION_IDEMPOTENCY":
        async with session_manager.session() as session:
            repo = PostgresNotificationRepository(session)
            reloaded = await repo.find_by_idempotency_key(
                OrganizationId(ctx.organization_id),
                NotificationIdempotencyKey(idem)
            )
            if reloaded:
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content=_notification_to_dto(reloaded)
                )

    notification = _handle_result(result)
    
    # If the handler returned an existing notification (due to idempotency or source event match inside the handler)
    if notification.id.value != notification_id:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=_notification_to_dto(notification)
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=_notification_to_dto(notification)
    )


@router.get("/api/notifications", status_code=status.HTTP_200_OK)
async def list_notifications(
    status_filter: Optional[str] = Query(None, alias="status"),
    channel_filter: Optional[str] = Query(None, alias="channel"),
    notification_type: Optional[str] = Query(None, alias="notification_type"),
    priority: Optional[str] = Query(None),
    recipient: Optional[str] = Query(None),
    reference: Optional[str] = Query(None),
    source_event_id: Optional[str] = Query(None, alias="source_event_id"),
    source_event_type: Optional[str] = Query(None, alias="source_event_type"),
    limit: int = Query(10, ge=1),
    offset: int = Query(0, ge=0),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """
    Lists tenant-isolated notifications, applying query filters.
    """
    async with session_manager.session() as session:
        repo = PostgresNotificationRepository(session)
        notifications = await repo.find_by_organization(OrganizationId(ctx.organization_id))

    dtos = []
    for n in notifications:
        if status_filter and n.status.value != status_filter:
            continue
        if channel_filter and n.channel.value != channel_filter:
            continue
        if notification_type and n.notification_type.value != notification_type:
            continue
        if priority and n.priority.value != priority:
            continue
        if recipient and n.recipient.destination != recipient:
            continue
        if reference and n.reference.value != reference:
            continue
        if source_event_id and n.source_event_id != source_event_id:
            continue
        if source_event_type and n.source_event_type != source_event_type:
            continue
        dtos.append(_notification_to_dto(n))

    # Apply pagination
    return dtos[offset : offset + limit]


@router.get("/api/notifications/{id}", status_code=status.HTTP_200_OK)
async def get_notification(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["MEMBER", "ADMIN", "OWNER"]))
):
    """
    Retrieves a specific notification details. Enforces tenant boundary
    by returning 404 for foreign notifications.
    """
    async with session_manager.session() as session:
        repo = PostgresNotificationRepository(session)
        notification = await repo.get_by_id(NotificationId(id))

    if not notification or notification.organization_id.value != ctx.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found."
        )

    return _notification_to_dto(notification)


@router.post("/api/notifications/{id}/process", status_code=status.HTTP_200_OK)
async def process_notification(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Request that a pending or retrying notification begin processing.
    """
    command = StartNotificationProcessingCommand(
        notification_id=id,
        organization_id=ctx.organization_id
    )

    async with container.start_notification_processing_handler() as handler:
        result = await handler.handle(command, ctx)

    notification = _handle_result(result)
    return _notification_to_dto(notification)


@router.post("/api/notifications/{id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_notification(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Cancels a pending notification instruction.
    """
    command = CancelNotificationCommand(
        notification_id=id,
        organization_id=ctx.organization_id
    )

    async with container.cancel_notification_handler() as handler:
        result = await handler.handle(command, ctx)

    notification = _handle_result(result)
    return _notification_to_dto(notification)


@router.post("/api/notifications/{id}/retry", status_code=status.HTTP_200_OK)
async def retry_notification(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Retries a failed retryable notification.
    """
    command = RetryNotificationCommand(
        notification_id=id,
        organization_id=ctx.organization_id
    )

    async with container.retry_notification_handler() as handler:
        result = await handler.handle(command, ctx)

    notification = _handle_result(result)
    return _notification_to_dto(notification)


@router.post("/api/notifications/{id}/replay", status_code=status.HTTP_200_OK)
async def replay_notification(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Replays a completed, failed, or dead-lettered notification.
    """
    command = ReplayNotificationCommand(
        notification_id=id,
        organization_id=ctx.organization_id
    )

    async with container.replay_notification_handler() as handler:
        result = await handler.handle(command, ctx)

    notification = _handle_result(result)
    return _notification_to_dto(notification)


@router.post("/api/notifications/{id}/redrive", status_code=status.HTTP_200_OK)
async def redrive_notification(
    id: str = Path(...),
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER"]))
):
    """
    Redrives/replays a dead-lettered notification.
    """
    command = ReplayNotificationCommand(
        notification_id=id,
        organization_id=ctx.organization_id
    )

    async with container.replay_notification_handler() as handler:
        result = await handler.handle(command, ctx)

    notification = _handle_result(result)
    return _notification_to_dto(notification)


class TriggerEscalationRequest(BaseModel):
    anomaly_title: str = Field(..., description="Title of the detected anomaly")
    severity: str = Field("CRITICAL", description="Severity level: CRITICAL, HIGH, MEDIUM")
    details: str = Field(..., description="Detailed description of the operational situation")
    channels: List[str] = Field(["EMAIL", "IN_APP"], description="Channels to dispatch alert through")


@router.post("/api/notifications/escalations", status_code=status.HTTP_201_CREATED)
async def trigger_operational_escalation(
    payload: TriggerEscalationRequest,
    ctx: ExecutionContext = Depends(require_roles(["ADMIN", "OWNER", "OPERATOR"]))
):
    """
    Triggers an operational escalation from Critical Anomaly -> Situation -> Notification -> Human.
    """
    from ....infrastructure.notification.email_service import email_service, EmailTemplateRenderer
    
    html = EmailTemplateRenderer.render_critical_anomaly(payload.anomaly_title, payload.severity, payload.details)
    msg = await email_service.send_email(
        recipient=f"operator@{ctx.organization_id[:8]}.com",
        subject=f"[{payload.severity}] Operational Alert: {payload.anomaly_title}",
        html_content=html
    )

    return {
        "status": "success",
        "escalation_id": f"esc_{uuid4().hex[:8]}",
        "anomaly_title": payload.anomaly_title,
        "severity": payload.severity,
        "human_operator_notified": msg.recipient,
        "channels_dispatched": payload.channels,
        "dispatched_at": msg.sent_at
    }

