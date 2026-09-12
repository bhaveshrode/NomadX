import json
from decimal import Decimal
from uuid import uuid4
from typing import List, Optional
from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from ...domain.settlement_reconciliation.repositories import ISettlementRepository
from ...domain.settlement_reconciliation.settlement import Settlement, SettlementStatus, SettlementEntry
from ...domain.settlement_reconciliation.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementIdempotencyKey,
    SettlementCurrency,
    SettlementAmount,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference
)
from ...domain.settlement_reconciliation.reconciliation import (
    ReconciliationStatus,
    ReconciliationResult,
    ReconciliationDiscrepancy
)
from ...domain.settlement_reconciliation.adjustments import (
    AdjustmentType,
    SettlementAdjustment
)
from ...domain.organization.value_objects import OrganizationId
from .models import (
    SettlementReconciliationDbModel,
    SettlementEntryDbModel,
    SettlementAdjustmentDbModel,
    SettlementDiscrepancyDbModel,
    OutboxEventDbModel
)

class PostgresSettlementRepository(ISettlementRepository):
    """
    SQLAlchemy-backed implementation of ISettlementRepository for the Reconciliation bounded context.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_domain(self, model: SettlementReconciliationDbModel) -> Settlement:
        """Maps DB model to domain aggregate root."""
        currency = SettlementCurrency(model.currency_code)
        
        # Map entries
        entries = []
        for entry_model in model.entries:
            entry = SettlementEntry(
                id=SettlementLineReference(entry_model.id),
                source_transaction_reference=entry_model.source_transaction_reference,
                source_context_type=entry_model.source_context_type,
                gross_amount=SettlementAmount(Decimal(entry_model.gross_amount)),
                fees_amount=SettlementAmount(Decimal(entry_model.fees_amount)),
                adjustments_amount=SettlementAmount(Decimal(entry_model.adjustments_amount)),
                net_amount=SettlementAmount(Decimal(entry_model.net_amount)),
                currency=currency,
                reconciliation_state=ReconciliationStatus(entry_model.reconciliation_state),
                status=entry_model.status
            )
            entries.append(entry)

        # Map adjustments
        adjustments = []
        for adj_model in model.adjustments:
            adj = SettlementAdjustment(
                id=adj_model.id,
                type=AdjustmentType(adj_model.type),
                description=adj_model.description,
                amount=SettlementAmount(Decimal(adj_model.amount)),
                currency=currency
            )
            adjustments.append(adj)

        # Map reconciliation result & discrepancies
        reconciliation_result = None
        if model.rec_verified_at is not None:
            discrepancies = []
            for disc_model in model.discrepancies:
                disc = ReconciliationDiscrepancy(
                    entry_reference=SettlementLineReference(disc_model.entry_reference),
                    discrepancy_type=disc_model.discrepancy_type,
                    expected_amount=SettlementAmount(Decimal(disc_model.expected_amount)),
                    actual_amount=SettlementAmount(Decimal(disc_model.actual_amount)),
                    description=disc_model.description,
                    resolved=disc_model.resolved
                )
                discrepancies.append(disc)

            reconciliation_result = ReconciliationResult(
                status=ReconciliationStatus(model.reconciliation_state),
                discrepancies=discrepancies,
                verified_at=model.rec_verified_at,
                description=model.rec_description or ""
            )

        props = {
            "organization_id": OrganizationId(model.organization_id),
            "reference": SettlementReference(model.reference),
            "merchant_reference": MerchantReference(model.merchant_reference),
            "period": SettlementPeriod(model.period_start, model.period_end),
            "currency": currency,
            "status": SettlementStatus(model.status),
            "reconciliation_state": ReconciliationStatus(model.reconciliation_state),
            "entries": entries,
            "adjustments": adjustments,
            "reconciliation_result": reconciliation_result,
            "idempotency_key": SettlementIdempotencyKey(model.idempotency_key),
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "processing_state": model.processing_state,
            "attempt_count": model.attempt_count,
            "max_attempts": model.max_attempts,
            "last_error": model.last_error,
            "failure_classification": model.failure_classification,
            "next_retry_at": model.next_retry_at,
            "processing_started_at": model.processing_started_at,
            "processing_completed_at": model.processing_completed_at,
            "dead_lettered_at": model.dead_lettered_at,
            "paused": model.paused
        }
        return Settlement(SettlementId(model.id), props)

    def _to_db(self, domain: Settlement) -> SettlementReconciliationDbModel:
        """Maps domain aggregate root to persistent model."""
        rec_verified_at = None
        rec_description = None
        if domain.reconciliation_result:
            rec_verified_at = domain.reconciliation_result.verified_at
            rec_description = domain.reconciliation_result.description

        return SettlementReconciliationDbModel(
            id=domain.id.value,
            organization_id=domain.organization_id.value,
            reference=domain.reference.value,
            merchant_reference=domain.merchant_reference.value,
            period_start=domain.period.start_date,
            period_end=domain.period.end_date,
            currency_code=domain.currency.code,
            status=domain.status.value,
            reconciliation_state=domain.reconciliation_state.value,
            idempotency_key=domain.idempotency_key.value,
            created_at=domain.created_at,
            updated_at=domain.updated_at,
            rec_verified_at=rec_verified_at,
            rec_description=rec_description,
            processing_state=domain.processing_state,
            attempt_count=domain.attempt_count,
            max_attempts=domain.max_attempts,
            last_error=domain.last_error,
            failure_classification=domain.failure_classification,
            next_retry_at=domain.next_retry_at,
            processing_started_at=domain.processing_started_at,
            processing_completed_at=domain.processing_completed_at,
            dead_lettered_at=domain.dead_lettered_at,
            paused=domain.paused
        )

    async def get_by_id(self, id: SettlementId, lock: bool = False) -> Optional[Settlement]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.id == id.value)
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        if lock:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def find_by_reference(self, reference: SettlementReference) -> Optional[Settlement]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.reference == reference.value)
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def find_by_organization(self, organization_id: OrganizationId) -> List[Settlement]:
        stmt = (
            select(SettlementReconciliationDbModel)
            .where(SettlementReconciliationDbModel.organization_id == organization_id.value)
            .options(
                selectinload(SettlementReconciliationDbModel.entries),
                selectinload(SettlementReconciliationDbModel.adjustments),
                selectinload(SettlementReconciliationDbModel.discrepancies)
            )
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

    async def save(self, settlement: Settlement) -> None:
        # 1. Finalization protection - check DB status first
        existing_res = await self._session.execute(
            select(SettlementReconciliationDbModel.status)
            .where(SettlementReconciliationDbModel.id == settlement.id.value)
        )
        existing_status = existing_res.scalar_one_or_none()
        if existing_status == SettlementStatus.COMPLETED.value:
            raise ValueError("CANNOT_MUTATE_FINALIZED_SETTLEMENT")

        db_model = self._to_db(settlement)
        try:
            await self._session.merge(db_model)

            # Save entries
            for entry in settlement.entries:
                entry_model = SettlementEntryDbModel(
                    id=entry.id.value,
                    settlement_id=settlement.id.value,
                    source_transaction_reference=entry.source_transaction_reference,
                    source_context_type=entry.source_context_type,
                    gross_amount=str(entry.gross_amount.amount),
                    fees_amount=str(entry.fees_amount.amount),
                    adjustments_amount=str(entry.adjustments_amount.amount),
                    net_amount=str(entry.net_amount.amount),
                    currency_code=entry.currency.code,
                    reconciliation_state=entry.reconciliation_state.value,
                    status=entry.status
                )
                await self._session.merge(entry_model)

            # Save adjustments
            for adj in settlement.adjustments:
                adj_model = SettlementAdjustmentDbModel(
                    id=adj.id,
                    settlement_id=settlement.id.value,
                    type=adj.type.value,
                    description=adj.description,
                    amount=str(adj.amount.amount),
                    currency_code=adj.currency.code
                )
                await self._session.merge(adj_model)

            # Save discrepancies (clearing existing first)
            await self._session.execute(
                delete(SettlementDiscrepancyDbModel)
                .where(SettlementDiscrepancyDbModel.settlement_id == settlement.id.value)
            )
            if settlement.reconciliation_result:
                for disc in settlement.reconciliation_result.discrepancies:
                    disc_model = SettlementDiscrepancyDbModel(
                        id=str(uuid4()),
                        settlement_id=settlement.id.value,
                        entry_reference=disc.entry_reference.value,
                        discrepancy_type=disc.discrepancy_type,
                        expected_amount=str(disc.expected_amount.amount),
                        actual_amount=str(disc.actual_amount.amount),
                        difference_amount=str(disc.difference.amount.amount),
                        description=disc.description,
                        resolved=disc.resolved
                    )
                    self._session.add(disc_model)

            await self._session.flush()

        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_settlements_rec_org_ref" in err_str or "settlement_reconciliations.reference" in err_str or "key (reference)" in err_str:
                raise ValueError("DUPLICATE_SETTLEMENT_REFERENCE")
            elif "uq_settlements_rec_org_idempotency" in err_str or "settlement_reconciliations.idempotency_key" in err_str or "key (organization_id, idempotency_key)" in err_str:
                raise ValueError("DUPLICATE_SETTLEMENT_IDEMPOTENCY")
            elif "uq_settlement_entries_rec_source_tx" in err_str or "settlement_entries_reconciliation.source_transaction_reference" in err_str:
                raise ValueError("DUPLICATE_ENTRY_SOURCE_TRANSACTION")
            raise err

        # Transactional Outbox
        from .repositories import current_correlation_id, current_causation_id
        for event in settlement.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "settlement_id": str(event.settlement_id.value),
                "organization_id": settlement.organization_id.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            if event.event_name == "SettlementCreated":
                payload_dict["merchant_reference"] = event.merchant_reference.value
            elif event.event_name == "SettlementEntryAdded":
                payload_dict["entry_reference"] = event.entry_reference.value
            elif event.event_name == "SettlementDiscrepancyDetected":
                payload_dict["discrepancy_count"] = event.discrepancy_count
            elif event.event_name == "SettlementAdjustmentApplied":
                payload_dict["adjustment_id"] = event.adjustment_id
            elif event.event_name == "SettlementProcessingDeadLettered":
                payload_dict["last_error"] = event.last_error
            elif event.event_name == "ReconciliationRetryScheduled":
                payload_dict["next_retry_at"] = event.next_retry_at.isoformat()
            elif event.event_name == "ReconciliationDiscrepancyResolved":
                payload_dict["entry_reference"] = event.entry_reference.value
            elif event.event_name == "SettlementProcessingFailed":
                payload_dict["error"] = event.error

            corr_id = current_correlation_id.get()
            caus_id = current_causation_id.get()
            if corr_id or caus_id:
                payload_dict["_metadata"] = {
                    "correlation_id": corr_id,
                    "causation_id": caus_id
                }

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        settlement.clear_domain_events()

    async def delete(self, id: SettlementId) -> None:
        # Check finalization protection
        existing_res = await self._session.execute(
            select(SettlementReconciliationDbModel.status)
            .where(SettlementReconciliationDbModel.id == id.value)
        )
        existing_status = existing_res.scalar_one_or_none()
        if existing_status == SettlementStatus.COMPLETED.value:
            raise ValueError("CANNOT_DELETE_FINALIZED_SETTLEMENT")

        stmt = delete(SettlementReconciliationDbModel).where(SettlementReconciliationDbModel.id == id.value)
        await self._session.execute(stmt)
