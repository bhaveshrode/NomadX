from decimal import Decimal
from typing import List, Optional
from uuid import uuid4
from datetime import datetime

from ...domain.organization.value_objects import OrganizationId
from ...domain.settlement_reconciliation.settlement import Settlement, SettlementStatus, SettlementEntry
from ...domain.settlement_reconciliation.repositories import ISettlementRepository
from ...domain.settlement_reconciliation.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAmount,
    SettlementCurrency,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference,
    SettlementIdempotencyKey
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
from ...domain.shared.result.result import Result
from ...presentation.api.dependencies.context import ExecutionContext

from .commands import (
    CreateSettlementCommand,
    OpenSettlementCommand,
    AddSettlementEntryCommand,
    AddSettlementAdjustmentCommand,
    BeginReconciliationCommand,
    ReconcileSettlementCommand,
    RecordSettlementDiscrepancyCommand,
    ResolveSettlementDiscrepancyCommand,
    CompleteSettlementCommand,
    CancelSettlementCommand
)
from .errors import SettlementReconciliationErrors


class CreateSettlementHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: CreateSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        # Idempotency lookup by reference
        existing = await self._repo.find_by_reference(SettlementReference(command.reference))
        if existing:
            if existing.organization_id.value != ctx.organization_id:
                return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access("Settlement reference belongs to another tenant."))
            if existing.idempotency_key.value == command.idempotency_key:
                return Result.ok(existing)
            return Result.fail(SettlementReconciliationErrors.duplicate_settlement("Settlement with reference already exists."))

        # Idempotency lookup by idempotency key
        all_settlements = await self._repo.find_by_organization(OrganizationId(command.organization_id))
        for s in all_settlements:
            if s.idempotency_key.value == command.idempotency_key:
                return Result.ok(s)

        try:
            settlement_id = SettlementId(command.settlement_id)
            reference = SettlementReference(command.reference)
            merchant_reference = MerchantReference(command.merchant_reference)
            period = SettlementPeriod(command.start_date, command.end_date)
            currency = SettlementCurrency(command.currency_code)
            idempotency_key = SettlementIdempotencyKey(command.idempotency_key)
        except ValueError as err:
            return Result.fail(SettlementReconciliationErrors.invalid_settlement_data(str(err)))

        create_res = Settlement.create(
            id=settlement_id,
            organization_id=OrganizationId(command.organization_id),
            reference=reference,
            merchant_reference=merchant_reference,
            period=period,
            currency=currency,
            idempotency_key=idempotency_key
        )
        if create_res.is_failure:
            return Result.fail(create_res.error)

        settlement = create_res.value
        try:
            await self._repo.save(settlement)
        except ValueError as err:
            err_str = str(err)
            if "DUPLICATE_SETTLEMENT_REFERENCE" in err_str or "DUPLICATE_SETTLEMENT_IDEMPOTENCY" in err_str:
                return Result.fail(SettlementReconciliationErrors.duplicate_settlement(err_str))
            raise err

        return Result.ok(settlement)


class OpenSettlementHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: OpenSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        res = settlement.open()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class AddSettlementEntryHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: AddSettlementEntryCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        if settlement.status == SettlementStatus.COMPLETED:
            return Result.fail(SettlementReconciliationErrors.settlement_already_completed())
        if settlement.status == SettlementStatus.CANCELLED:
            return Result.fail(SettlementReconciliationErrors.settlement_already_cancelled())

        if command.currency_code != settlement.currency.code:
            return Result.fail(SettlementReconciliationErrors.settlement_currency_mismatch())

        try:
            entry = SettlementEntry(
                id=SettlementLineReference(command.entry_reference),
                source_transaction_reference=command.source_transaction_reference,
                source_context_type=command.source_context_type,
                gross_amount=SettlementAmount(command.gross_amount),
                fees_amount=SettlementAmount(command.fees_amount),
                adjustments_amount=SettlementAmount(command.adjustments_amount),
                net_amount=SettlementAmount(command.net_amount),
                currency=SettlementCurrency(command.currency_code)
            )
        except ValueError as err:
            return Result.fail(SettlementReconciliationErrors.settlement_entry_invalid(str(err)))

        add_res = settlement.add_entry(entry)
        if add_res.is_failure:
            if add_res.error.code == "VALIDATION_ERROR" or "cannot add" in add_res.error.message.lower():
                if entry.currency.code != settlement.currency.code:
                    return Result.fail(SettlementReconciliationErrors.settlement_currency_mismatch())
                for existing in settlement.entries[:-1]: # exclude the current failed candidate if appended, but domain add_entry didn't append it on failure. We can check all existing entries
                    if existing.source_transaction_reference == entry.source_transaction_reference:
                        return Result.fail(SettlementReconciliationErrors.duplicate_settlement_entry())
                # Check duplicate entry manually across all entries
                for existing in settlement.entries:
                    if existing.source_transaction_reference == entry.source_transaction_reference:
                        return Result.fail(SettlementReconciliationErrors.duplicate_settlement_entry())
                return Result.fail(SettlementReconciliationErrors.settlement_entry_invalid(add_res.error.message))
            if "duplicate" in add_res.error.message.lower() or "already" in add_res.error.message.lower():
                return Result.fail(SettlementReconciliationErrors.duplicate_settlement_entry())
            return Result.fail(add_res.error)

        try:
            await self._repo.save(settlement)
        except ValueError as err:
            if "DUPLICATE_ENTRY_SOURCE_TRANSACTION" in str(err):
                return Result.fail(SettlementReconciliationErrors.duplicate_settlement_entry())
            raise err

        return Result.ok(settlement)


class AddSettlementAdjustmentHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: AddSettlementAdjustmentCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        if settlement.status == SettlementStatus.COMPLETED:
            return Result.fail(SettlementReconciliationErrors.settlement_already_completed())
        if settlement.status == SettlementStatus.CANCELLED:
            return Result.fail(SettlementReconciliationErrors.settlement_already_cancelled())

        if command.currency_code != settlement.currency.code:
            return Result.fail(SettlementReconciliationErrors.settlement_currency_mismatch())

        try:
            adj = SettlementAdjustment(
                id=command.adjustment_id,
                type=AdjustmentType(command.type),
                description=command.description,
                amount=SettlementAmount(command.amount),
                currency=SettlementCurrency(command.currency_code)
            )
        except ValueError as err:
            return Result.fail(SettlementReconciliationErrors.invalid_settlement_data(str(err)))

        apply_res = settlement.apply_adjustment(adj)
        if apply_res.is_failure:
            return Result.fail(apply_res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class BeginReconciliationHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: BeginReconciliationCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        res = settlement.start_reconciliation()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class ReconcileSettlementHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: ReconcileSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        try:
            discrepancies = []
            for d in command.discrepancies:
                disc = ReconciliationDiscrepancy(
                    entry_reference=SettlementLineReference(d["entry_reference"]),
                    discrepancy_type=d["discrepancy_type"],
                    expected_amount=SettlementAmount(d["expected_amount"]),
                    actual_amount=SettlementAmount(d["actual_amount"]),
                    description=d["description"],
                    resolved=d.get("resolved", False)
                )
                discrepancies.append(disc)

            result = ReconciliationResult(
                status=ReconciliationStatus(command.status),
                discrepancies=discrepancies,
                verified_at=command.verified_at,
                description=command.description
            )
        except ValueError as err:
            return Result.fail(SettlementReconciliationErrors.invalid_reconciliation_state(str(err)))

        res = settlement.reconcile(result)
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class RecordSettlementDiscrepancyHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: RecordSettlementDiscrepancyCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        if settlement.status not in (SettlementStatus.RECONCILING, SettlementStatus.DISCREPANCY):
            return Result.fail(SettlementReconciliationErrors.invalid_settlement_state("Discrepancy can only be recorded during reconciliation."))

        try:
            disc = ReconciliationDiscrepancy(
                entry_reference=SettlementLineReference(command.entry_reference),
                discrepancy_type=command.discrepancy_type,
                expected_amount=SettlementAmount(command.expected_amount),
                actual_amount=SettlementAmount(command.actual_amount),
                description=command.description
            )
        except ValueError as err:
            return Result.fail(SettlementReconciliationErrors.invalid_settlement_data(str(err)))

        discrepancies = [disc]
        verified_at = datetime.utcnow()
        description = "Manually recorded discrepancy"

        if settlement.reconciliation_result:
            discrepancies = list(settlement.reconciliation_result.discrepancies) + discrepancies
            verified_at = settlement.reconciliation_result.verified_at
            description = settlement.reconciliation_result.description

        new_result = ReconciliationResult(
            status=ReconciliationStatus.DISCREPANCY,
            discrepancies=discrepancies,
            verified_at=verified_at,
            description=description
        )

        res = settlement.reconcile(new_result)
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class ResolveSettlementDiscrepancyHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: ResolveSettlementDiscrepancyCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        res = settlement.resolve_discrepancy(SettlementLineReference(command.entry_reference))
        if res.is_failure:
            if "not found" in res.error.message.lower():
                return Result.fail(SettlementReconciliationErrors.settlement_discrepancy_not_found())
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class CompleteSettlementHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: CompleteSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        # Check unresolved discrepancies
        if settlement.reconciliation_result:
            for discrepancy in settlement.reconciliation_result.discrepancies:
                if not discrepancy.resolved:
                    return Result.fail(SettlementReconciliationErrors.settlement_discrepancy_exists())

        # Run cross-context financial consistency checker before completing
        from ...bootstrap.container import container
        async with container.financial_consistency_query_service() as consistency_svc:
            report = await consistency_svc.check_financial_consistency(OrganizationId(ctx.organization_id))
            blocked_violations = []
            entry_ids = [entry.id.value for entry in settlement.entries]
            for v in report.get("violations", []):
                if v.get("context") == "Settlement" and v.get("entity_id") == settlement.id.value:
                    blocked_violations.append(v)
                elif v.get("entity_id") == settlement.reference.value:
                    blocked_violations.append(v)
                elif v.get("entity_id") in entry_ids:
                    blocked_violations.append(v)
            if blocked_violations:
                msg = f"Cannot complete settlement due to cross-context consistency violations: {blocked_violations[0]['description']}"
                return Result.fail(SettlementReconciliationErrors.invalid_settlement_state(msg))

        res = settlement.complete()
        if res.is_failure:
            if "not balanced" in res.error.message.lower():
                return Result.fail(SettlementReconciliationErrors.settlement_not_balanced())
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class CancelSettlementHandler:
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: CancelSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementReconciliationErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementReconciliationErrors.unauthorized_organization_access())

        res = settlement.cancel()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)
