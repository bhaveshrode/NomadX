from datetime import datetime
import json
import logging
from uuid import uuid4
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.database.models import TelemetryEventDbModel
from ...infrastructure.database.repositories import current_correlation_id, current_causation_id

logger = logging.getLogger("nomadx.telemetry")


class TelemetryService:
    """
    Unified Telemetry Service for recording structured events, API operations,
    worker tasks, and state transitions. Logs structured data and persists to the database.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def record_event(
        self,
        component: str,
        operation: str,
        message: str,
        severity: str = "INFO",
        entity_id: Optional[str] = None,
        previous_state: Optional[str] = None,
        resulting_state: Optional[str] = None,
        status: str = "SUCCESS",
        duration_ms: Optional[int] = None,
        organization_id: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None
    ) -> str:
        # Retrieve correlation / causation from context variables if not explicitly provided
        final_correlation_id = correlation_id or current_correlation_id.get() or str(uuid4())
        final_causation_id = causation_id or current_causation_id.get() or "SYSTEM"

        # Construct log payload
        log_payload = {
            "component": component,
            "operation": operation,
            "message": message,
            "severity": severity,
            "entity_id": entity_id,
            "previous_state": previous_state,
            "resulting_state": resulting_state,
            "status": status,
            "duration_ms": duration_ms,
            "correlation_id": final_correlation_id,
            "causation_id": final_causation_id,
            "organization_id": organization_id,
            "metadata": extra_metadata
        }
        
        # Output structured log
        if severity == "INFO":
            logger.info(json.dumps(log_payload))
        elif severity == "WARNING":
            logger.warning(json.dumps(log_payload))
        elif severity == "ERROR":
            logger.error(json.dumps(log_payload))
        elif severity == "CRITICAL":
            logger.critical(json.dumps(log_payload))

        # Persist to database if organization context is available
        event_id = str(uuid4())
        if organization_id:
            event = TelemetryEventDbModel(
                id=event_id,
                timestamp=datetime.utcnow(),
                component=component,
                operation=operation,
                message=message,
                severity=severity,
                entity_id=entity_id,
                previous_state=previous_state,
                resulting_state=resulting_state,
                status=status,
                duration_ms=duration_ms,
                correlation_id=final_correlation_id,
                causation_id=final_causation_id,
                organization_id=organization_id,
                extra_metadata=json.dumps(extra_metadata) if extra_metadata else None
            )
            self._session.add(event)
            await self._session.flush()

        return event_id
