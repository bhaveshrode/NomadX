import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import OutboxEventDbModel, SettlementReconciliationDbModel
from ...domain.shared.clock.clock import Clock
from ...infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository
from ...domain.organization.value_objects import OrganizationId
from ...domain.settlement_reconciliation.settlement import Settlement, SettlementEntry, SettlementStatus
from ...domain.settlement_reconciliation.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementCurrency,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference,
    SettlementIdempotencyKey,
    SettlementAmount
)
from ...presentation.api.dependencies.context import ExecutionContext


class SettlementEventProcessor:
    """
    Processor that listens to cross-context financial outbox events (Payments, Refunds, etc.)
    and automatically updates/populates settlement reconciliation batches.
    """
    async def process_financial_events(self, limit: int = 50) -> int:
        event_types = ["PaymentReceived", "PaymentReconciled", "PaymentRefunded", "PaymentReversed"]
        current_time = Clock.now()
        current_time_naive = current_time.replace(tzinfo=None) if current_time.tzinfo else current_time

        async with session_manager.session() as session:
            stmt = (
                select(OutboxEventDbModel)
                .where(OutboxEventDbModel.event_type.in_(event_types))
                .where(OutboxEventDbModel.status.in_(["PENDING", "RETRYING"]))
                .where(
                    (OutboxEventDbModel.next_retry_at == None) |
                    (OutboxEventDbModel.next_retry_at <= current_time_naive)
                )
                .limit(limit)
            )
            
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)

            res = await session.execute(stmt)
            events = res.scalars().all()

            if not events:
                return 0

            # Claim events
            for event in events:
                event.status = "PROCESSING"
            await session.commit()

        processed_count = 0
        for event in events:
            async with session_manager.session() as event_session:
                # Reload event
                ev_stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == event.id)
                ev_res = await event_session.execute(ev_stmt)
                ev_model = ev_res.scalar_one_or_none()
                if not ev_model:
                    continue

                repo = PostgresSettlementRepository(event_session)

                try:
                    payload = json.loads(ev_model.payload)
                except Exception:
                    payload = {}

                # Setup correlation/causation metadata propagation
                from ...infrastructure.database.repositories import (
                    current_correlation_id,
                    current_causation_id
                )
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
                    amount_str = payload.get("allocated_amount") or payload.get("amount") or "0.00"
                    currency_code = payload.get("currency") or payload.get("currency_code") or "USDC"
                    tx_ref = payload.get("transaction_reference") or payload.get("payment_id") or f"TX-AUTO-{ev_model.id[:8]}"
                    observed_at_str = payload.get("observed_at") or payload.get("created_at")

                    if not org_id_str:
                        # Skip if no organization ID
                        ev_model.processed = True
                        ev_model.processed_at = Clock.now().replace(tzinfo=None)
                        ev_model.status = "COMPLETED"
                        await event_session.commit()
                        processed_count += 1
                        continue

                    # Parse date
                    if observed_at_str:
                        try:
                            observed_at = datetime.fromisoformat(observed_at_str.replace("Z", "+00:00"))
                        except Exception:
                            observed_at = Clock.now()
                    else:
                        observed_at = Clock.now()

                    # Find existing DRAFT or OPEN settlement batch for this org & currency containing observed_at
                    obs_naive = observed_at.replace(tzinfo=None)
                    settlement_stmt = (
                        select(SettlementReconciliationDbModel)
                        .where(
                            SettlementReconciliationDbModel.organization_id == org_id_str,
                            SettlementReconciliationDbModel.currency_code == currency_code,
                            SettlementReconciliationDbModel.status.in_(["DRAFT", "OPEN"]),
                            SettlementReconciliationDbModel.period_start <= obs_naive,
                            SettlementReconciliationDbModel.period_end >= obs_naive
                        )
                        .options(
                            selectinload(SettlementReconciliationDbModel.entries),
                            selectinload(SettlementReconciliationDbModel.adjustments),
                            selectinload(SettlementReconciliationDbModel.discrepancies)
                        )
                    )
                    s_res = await event_session.execute(settlement_stmt)
                    s_model = s_res.scalar_one_or_none()

                    if s_model:
                        settlement = repo._to_domain(s_model)
                    else:
                        # Auto-create monthly draft settlement
                        first_day = observed_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                        # Last day of month
                        next_month = first_day + timedelta(days=32)
                        last_day = next_month.replace(day=1) - timedelta(seconds=1)

                        settlement_id = str(uuid4())
                        reference = f"SET-AUTO-{org_id_str[:4].upper()}-{observed_at.strftime('%Y%m')}-{uuid4().hex[:4].upper()}"
                        
                        settlement_res = Settlement.create(
                            id=SettlementId(settlement_id),
                            organization_id=OrganizationId(org_id_str),
                            reference=SettlementReference(reference),
                            merchant_reference=MerchantReference("MERCH-AUTO"),
                            period=SettlementPeriod(first_day, last_day),
                            currency=SettlementCurrency(currency_code),
                            idempotency_key=SettlementIdempotencyKey(f"idem-auto-{uuid4().hex[:8]}")
                        )
                        if settlement_res.is_failure:
                            raise ValueError(f"Failed to auto-create settlement: {settlement_res.error.message}")
                        settlement = settlement_res.value
                        settlement.open()

                    # Check idempotency: does entry for tx_ref already exist?
                    exists = any(e.source_transaction_reference == tx_ref for e in settlement.entries)

                    if not exists:
                        gross = Decimal(amount_str)
                        if ev_model.event_type in ("PaymentRefunded", "PaymentReversed"):
                            gross = -abs(gross)

                        entry = SettlementEntry(
                            id=SettlementLineReference(f"LINE-{uuid4().hex[:8]}"),
                            source_transaction_reference=tx_ref,
                            source_context_type="PAYMENT",
                            gross_amount=SettlementAmount(gross),
                            fees_amount=SettlementAmount(Decimal("0.00")),
                            adjustments_amount=SettlementAmount(Decimal("0.00")),
                            net_amount=SettlementAmount(gross),
                            currency=SettlementCurrency(currency_code)
                        )
                        add_res = settlement.add_entry(entry)
                        if add_res.is_failure:
                            raise ValueError(f"Failed to add entry: {add_res.error.message}")

                        await repo.save(settlement)

                    ev_model.processed = True
                    ev_model.processed_at = Clock.now().replace(tzinfo=None)
                    ev_model.status = "COMPLETED"
                    ev_model.error_message = None
                    await event_session.commit()
                    processed_count += 1

                except Exception as e:
                    await event_session.rollback()
                    # Fail event
                    async with session_manager.session() as fail_session:
                        ev_fail_stmt = select(OutboxEventDbModel).where(OutboxEventDbModel.id == event.id)
                        ev_fail_res = await fail_session.execute(ev_fail_stmt)
                        ev_fail_model = ev_fail_res.scalar_one_or_none()
                        if ev_fail_model:
                            ev_fail_model.attempts += 1
                            if ev_fail_model.attempts >= 3:
                                ev_fail_model.status = "DEAD_LETTERED"
                                ev_fail_model.dead_lettered_at = Clock.now().replace(tzinfo=None)
                            else:
                                ev_fail_model.status = "RETRYING"
                                ev_fail_model.next_retry_at = Clock.now().replace(tzinfo=None) + timedelta(seconds=60)
                            ev_fail_model.error_message = str(e)
                            await fail_session.commit()
                finally:
                    current_correlation_id.reset(token_corr)
                    current_causation_id.reset(token_caus)

        return processed_count

    async def recover_stuck_events(self, timeout_minutes: int = 5) -> int:
        """Finds and resets outbox events stuck in PROCESSING state for > timeout_minutes."""
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        
        async with session_manager.session() as session:
            event_types = ["PaymentReceived", "PaymentReconciled", "PaymentRefunded", "PaymentReversed"]
            stmt = (
                select(OutboxEventDbModel)
                .where(
                    OutboxEventDbModel.event_type.in_(event_types),
                    OutboxEventDbModel.status == "PROCESSING",
                    OutboxEventDbModel.created_at < cutoff
                )
            )
            
            dialect_name = session.bind.dialect.name if session.bind else "postgresql"
            if dialect_name != "sqlite":
                stmt = stmt.with_for_update(skip_locked=True)
                
            res = await session.execute(stmt)
            stuck_events = res.scalars().all()
            
            recovered_count = 0
            for event in stuck_events:
                event.status = "PENDING"
                event.processed = False
                event.error_message = "Stuck event execution recovered."
                recovered_count += 1
                
            await session.commit()
            return recovered_count
