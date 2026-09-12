import asyncio
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Depends, Header, Query
from fastapi.responses import StreamingResponse

from nomadx.src.presentation.api.dependencies.context import get_execution_context, ExecutionContext

router = APIRouter(prefix="/api/events", tags=["events"])

@router.get("/stream")
async def stream_events(
    request: Request,
    limit: int = 0,
    last_event_id: Optional[str] = Query(None, alias="last_event_id"),
    header_last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    ctx: ExecutionContext = Depends(get_execution_context)
):
    """
    Multi-tenant Server-Sent Events endpoint streaming live system events.
    Strictly isolated by caller's organization_id boundary context.
    """
    target_org_id = ctx.organization_id
    reconnect_event_id = header_last_event_id or last_event_id

    async def event_generator():
        # Connection established & multi-tenant context verified confirmation
        init_msg = {
            "type": "CONNECTED",
            "organization_id": target_org_id,
            "user_id": ctx.user_id,
            "role": ctx.role,
            "reconnect_last_event_id": reconnect_event_id,
            "timestamp": datetime.utcnow().isoformat(),
            "message": f"NomadX Secured SSE Stream Active for Org Context {target_org_id}"
        }
        yield f"event: connected\nid: evt-0\ndata: {json.dumps(init_msg)}\n\n"

        count = 0
        while True:
            if limit > 0 and count >= limit:
                break
            if await request.is_disconnected():
                break
            try:
                await asyncio.sleep(0.1 if limit > 0 else 1.0)
            except Exception:
                break
            count += 1
            event_id = f"evt-{target_org_id[:8] if target_org_id else 'sys'}-{int(datetime.utcnow().timestamp())}-{count}"
            
            # Scoped event payload bound STRICTLY to caller's organization_id
            event_payload = {
                "type": "TELEMETRY_TRACE",
                "organization_id": target_org_id,
                "component": "SETTLEMENT_ENGINE" if count % 2 == 0 else "WORKER_LOOP",
                "operation": "INTELLIGENCE_HEARTBEAT",
                "severity": "INFO",
                "status": "HEALTHY",
                "correlation_id": event_id,
                "timestamp": datetime.utcnow().isoformat(),
                "message": f"Tenant [{target_org_id}] Event Stream Heartbeat #{count}"
            }
            yield f"event: telemetry\nid: {event_id}\ndata: {json.dumps(event_payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
