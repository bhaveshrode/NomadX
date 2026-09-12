import json
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import OutboxEventDbModel, PaymentAllocationDbModel
from ...domain.invoice import InvoiceId
from ...domain.receivable.value_objects import ReceivableId
from ...domain.payment.value_objects import PaymentId
from ...application.receivable.commands import CreateReceivableCommand, ApplyPaymentAllocationCommand
from ...application.receivable.handlers import CreateReceivableHandler, ApplyPaymentAllocationHandler
from ...infrastructure.database.repositories import (
    PostgresReceivableRepository,
    PostgresInvoiceRepository,
    PostgresPaymentAllocationRepository
)
from ...presentation.api.dependencies.context import ExecutionContext


class ReceivableWorker:
    """
    Worker coordinating background synchronization of Receivables.
    Processes InvoiceIssued and PaymentReconciled outbox events.
    """
    async def process_pending_events(self) -> int:
        """
        Polls unprocessed AR events from the transactional outbox,
        dispatches to handlers transactionally, and updates outbox status.
        """
        from .ar_event_processor import AREventProcessor
        processor = AREventProcessor()
        return await processor.process_payment_events()
