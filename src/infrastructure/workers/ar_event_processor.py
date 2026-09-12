import json
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import OutboxEventDbModel, ReceivableDbModel
from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.repositories import PostgresReceivableRepository
from ...domain.receivable.value_objects import ReceivableId
from ...domain.invoice.value_objects import InvoiceId, Money, Currency, CurrencyType
from ...presentation.api.dependencies.context import ExecutionContext


class AREventProcessor:
    """
    Background worker reacting to payment events (received, reconciled, matched, refunded, reversed).
    Applies allocations and reversals to target Receivables idempotently.
    """
    async def process_payment_events(self, limit: int = 50) -> int:
        """
        Polls unprocessed outbox events, claims them, and processes
        them transactionally with isolated errors, transient/permanent failure mapping, and retries.
        """
        event_types = [
            "InvoiceIssued", "InvoiceVoided", "InvoiceCancelled",
            "PaymentReceived", "PaymentReconciled", "PaymentPartiallyMatched", 
            "PaymentRefunded", "PaymentReversed", "SettlementCompleted"
        ]
        current_time = Clock.now()
        current_time_naive = current_time.replace(tzinfo=None) if current_time.tzinfo else current_time

        async with session_manager.session() as session:
            # Query for unprocessed events
            stmt = (
                select(OutboxEventDbModel)
                .where(OutboxEventDbModel.event_type.in_(event_types))
                .where(OutboxEventDbModel.status.in_(["PENDING", "RETRYING"]))
                .where(
                    (OutboxEventDbModel.next_retry_at == None) |
                    (OutboxEventDbModel.next_retry_at <= current_time_naive)
                )
            )

            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)
            stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            events = result.scalars().all()

            if not events:
                return 0

            # Mark all as PROCESSING and commit immediately to claim them
            for event in events:
                event.status = "PROCESSING"
            await session.commit()

        # Process each claimed event in its own transaction
        processed_count = 0
        for event in events:
            async with session_manager.session() as event_session:
                # Reload event inside this session
                ev_stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == event.id)
                ev_res = await event_session.execute(ev_stmt)
                ev_model = ev_res.scalar_one_or_none()
                if not ev_model:
                    continue

                receivable_repo = PostgresReceivableRepository(event_session)
                
                # Import context variables for event lineage tracking
                from ...infrastructure.database.repositories import (
                    current_correlation_id,
                    current_causation_id
                )

                try:
                    payload = json.loads(ev_model.payload)
                except Exception:
                    payload = {}

                # Setup correlation metadata propagation
                metadata = payload.get("_metadata", {})
                correlation_id = (
                    metadata.get("correlation_id") or 
                    payload.get("correlation_id") or 
                    payload.get("event_id") or 
                    ev_model.id
                )
                causation_id = ev_model.id

                token_corr = current_correlation_id.set(correlation_id)
                token_caus = current_causation_id.set(causation_id)

                try:
                    org_id_str = payload.get("organization_id")

                    if ev_model.event_type == "InvoiceIssued":
                        invoice_id = payload.get("invoice_id")
                        if not invoice_id:
                            ev_model.processed = True
                            ev_model.processed_at = Clock.now().replace(tzinfo=None)
                            ev_model.status = "COMPLETED"
                            await event_session.commit()
                            continue

                        # Check idempotency: does receivable exist?
                        receivable = await receivable_repo.find_by_invoice(InvoiceId(invoice_id))
                        if not receivable:
                            from ...application.receivable.commands import CreateReceivableCommand
                            from ...application.receivable.handlers import CreateReceivableHandler
                            from ...infrastructure.database.repositories import PostgresInvoiceRepository
                            from uuid import uuid4
                            
                            invoice_repo = PostgresInvoiceRepository(event_session)
                            create_handler = CreateReceivableHandler(receivable_repo, invoice_repo)
                            
                            ctx = ExecutionContext(
                                user_id="ar_event_processor",
                                organization_id=org_id_str,
                                role="SYSTEM"
                            )
                            
                            cmd = CreateReceivableCommand(
                                receivable_id=str(uuid4()),
                                organization_id=org_id_str,
                                invoice_id=invoice_id
                            )
                            
                            res = await create_handler.handle(cmd, ctx)
                            if res.is_failure:
                                raise ValueError(f"Failed to create receivable: {res.error.message}")

                    elif ev_model.event_type in ("InvoiceVoided", "InvoiceCancelled"):
                        invoice_id = payload.get("invoice_id")
                        if not invoice_id:
                            ev_model.processed = True
                            ev_model.processed_at = Clock.now().replace(tzinfo=None)
                            ev_model.status = "COMPLETED"
                            await event_session.commit()
                            continue

                        receivable = await receivable_repo.find_by_invoice(InvoiceId(invoice_id))
                        if receivable:
                            if receivable.status.value != "WRITTEN_OFF":
                                write_res = receivable.write_off("Invoice cancelled", Clock.now())
                                if write_res.is_failure:
                                    raise ValueError(f"Failed to write off receivable: {write_res.error.message}")
                                await receivable_repo.save(receivable)
                                # Cancel pending actions
                                from ...infrastructure.database.repositories import PostgresCollectionActionRepository
                                collection_action_repo = PostgresCollectionActionRepository(event_session)
                                await collection_action_repo.cancel_pending_actions(receivable.id)
                        else:
                            raise ValueError(f"Receivable not found for invoice {invoice_id} to cancel. Retrying.")

                    elif ev_model.event_type in ("PaymentReceived", "PaymentReconciled", "PaymentPartiallyMatched"):
                        payment_id = payload.get("payment_id")
                        invoice_id = payload.get("invoice_id")
                        allocated_amount_str = payload.get("allocated_amount") or payload.get("amount")
                        currency_code = payload.get("currency") or payload.get("currency_code")

                        if not invoice_id or not allocated_amount_str:
                            ev_model.processed = True
                            ev_model.processed_at = Clock.now().replace(tzinfo=None)
                            ev_model.status = "COMPLETED"
                            await event_session.commit()
                            processed_count += 1
                            continue

                        receivable = await receivable_repo.find_by_invoice(InvoiceId(invoice_id))
                        if not receivable:
                            raise ValueError(f"Receivable not found for invoice {invoice_id}. Retrying.")

                        print(f"--- DEBUG: loaded receivable id={receivable.id.value}, status={receivable.status}, outstanding={receivable.outstanding_amount.amount}, allocations={len(receivable.allocations)}")

                        if not currency_code:
                            currency_code = receivable.currency.code

                        # Idempotency Check: check if allocation already exists
                        existing_alloc = any(
                            a.allocation_reference == ev_model.id 
                            for a in receivable.allocations
                        )
                        if not existing_alloc:
                            allocated_amount = Decimal(str(allocated_amount_str))
                            currency_type = CurrencyType.DIGITAL_ASSET if currency_code in ("USDC", "USDT") else CurrencyType.FIAT
                            money = Money(allocated_amount, Currency(currency_code, currency_type))

                            # Cap allocation to outstanding amount to support overpayments without negative balance
                            if ev_model.event_type in ("PaymentReceived", "PaymentPartiallyMatched"):
                                if money.amount > receivable.outstanding_amount.amount:
                                    money = receivable.outstanding_amount

                            alloc_res = receivable.allocate_payment(
                                payment_id=payment_id or "unknown",
                                amount=money,
                                allocation_reference=ev_model.id,
                                occurred_on=Clock.now()
                            )
                            if alloc_res.is_failure:
                                raise ValueError(f"Failed to allocate payment: {alloc_res.error.message}")
                            await receivable_repo.save(receivable)

                            # If settled, cancel pending collection actions
                            if receivable.status.value == "SETTLED":
                                from ...infrastructure.database.repositories import PostgresCollectionActionRepository
                                collection_action_repo = PostgresCollectionActionRepository(event_session)
                                await collection_action_repo.cancel_pending_actions(receivable.id)

                    elif ev_model.event_type in ("PaymentRefunded", "PaymentReversed"):
                        payment_id = payload.get("payment_id")
                        invoice_id = payload.get("invoice_id")
                        allocated_amount_str = payload.get("allocated_amount") or payload.get("amount")
                        currency_code = payload.get("currency") or payload.get("currency_code")

                        if not invoice_id or not allocated_amount_str:
                            ev_model.processed = True
                            ev_model.processed_at = Clock.now().replace(tzinfo=None)
                            ev_model.status = "COMPLETED"
                            await event_session.commit()
                            processed_count += 1
                            continue

                        receivable = await receivable_repo.find_by_invoice(InvoiceId(invoice_id))
                        if not receivable:
                            raise ValueError(f"Receivable not found for invoice {invoice_id}. Retrying.")

                        if not currency_code:
                            currency_code = receivable.currency.code

                        # Idempotency Check: check if refund allocation already exists
                        import uuid
                        refund_ref = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"refund-{ev_model.id}"))
                        existing_refund = any(
                            a.allocation_reference == refund_ref
                            for a in receivable.allocations
                        )
                        if not existing_refund:
                            allocated_amount = Decimal(str(allocated_amount_str))
                            currency_type = CurrencyType.DIGITAL_ASSET if currency_code in ("USDC", "USDT") else CurrencyType.FIAT
                            money = Money(allocated_amount, Currency(currency_code, currency_type))

                            refund_res = receivable.refund_payment(
                                payment_id=payment_id or "unknown",
                                amount=money,
                                allocation_reference=refund_ref,
                                occurred_on=Clock.now()
                            )
                            if refund_res.is_failure:
                                raise ValueError(f"Failed to refund payment: {refund_res.error.message}")
                            await receivable_repo.save(receivable)

                    elif ev_model.event_type == "SettlementCompleted":
                        pass

                    ev_model.processed = True
                    ev_model.processed_at = Clock.now().replace(tzinfo=None)
                    ev_model.status = "COMPLETED"
                    ev_model.error_message = None

                    await event_session.commit()
                    processed_count += 1

                except Exception as e:
                    import traceback
                    print(f"--- EXCEPTION IN PROCESS PAYMENT EVENTS:\n{traceback.format_exc()}")
                    await event_session.rollback()

                    # Save failure in a separate transaction
                    async with session_manager.session() as fail_session:
                        ev_fail_stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == event.id)
                        ev_fail_res = await fail_session.execute(ev_fail_stmt)
                        ev_fail_model = ev_fail_res.scalar_one_or_none()
                        if ev_fail_model:
                            ev_fail_model.attempts += 1
                            error_str = str(e)
                            is_permanent = "not found" not in error_str.lower() and (
                                "invalid" in error_str.lower() or 
                                "unsupported" in error_str.lower() or
                                isinstance(e, KeyError)
                            )
                            
                            if is_permanent or ev_fail_model.attempts >= 3:
                                ev_fail_model.status = "DEAD_LETTERED"
                                ev_fail_model.dead_lettered_at = Clock.now().replace(tzinfo=None)
                                ev_fail_model.next_retry_at = None
                            else:
                                ev_fail_model.status = "RETRYING"
                                delay_map = {1: 60, 2: 300, 3: 900}
                                delay_seconds = delay_map.get(ev_fail_model.attempts, 900)
                                ev_fail_model.next_retry_at = Clock.now().replace(tzinfo=None) + timedelta(seconds=delay_seconds)
                            
                            ev_fail_model.error_message = error_str
                            await fail_session.commit()
                finally:
                    current_correlation_id.reset(token_corr)
                    current_causation_id.reset(token_caus)

        return processed_count

    async def recover_stuck_events(self, threshold_minutes: int = 15) -> int:
        """Finds outbox payment events stuck in PROCESSING status and resets them."""
        cutoff = Clock.now() - timedelta(minutes=threshold_minutes)
        cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
        event_types = [
            "InvoiceIssued", "InvoiceVoided", "InvoiceCancelled",
            "PaymentReceived", "PaymentReconciled", "PaymentPartiallyMatched", 
            "PaymentRefunded", "PaymentReversed", "SettlementCompleted"
        ]

        async with session_manager.session() as session:
            stmt = select(OutboxEventDbModel).where(
                OutboxEventDbModel.event_type.in_(event_types),
                OutboxEventDbModel.status == "PROCESSING",
                OutboxEventDbModel.created_at <= cutoff_naive
            )
            res = await session.execute(stmt)
            stuck_models = res.scalars().all()
            if not stuck_models:
                return 0

            count = 0
            for model in stuck_models:
                model.attempts += 1
                if model.attempts >= 3:
                    model.status = "DEAD_LETTERED"
                    model.dead_lettered_at = Clock.now().replace(tzinfo=None)
                    model.next_retry_at = None
                else:
                    model.status = "RETRYING"
                    delay_map = {1: 60, 2: 300, 3: 900}
                    delay_seconds = delay_map.get(model.attempts, 900)
                    model.next_retry_at = Clock.now().replace(tzinfo=None) + timedelta(seconds=delay_seconds)
                
                model.error_message = "Event recovered from stuck PROCESSING state."
                count += 1

            await session.commit()
            return count
