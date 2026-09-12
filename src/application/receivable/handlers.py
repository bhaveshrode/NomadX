from uuid import uuid4
from decimal import Decimal
from typing import Optional

from ...domain.shared.result.result import Result, ResultError
from ...domain.shared.clock.clock import Clock
from ...domain.organization.value_objects import OrganizationId
from ...domain.invoice.value_objects import InvoiceId, Money, Currency, CurrencyType
from ...domain.invoice.repositories import IInvoiceRepository
from ...domain.receivable.value_objects import ReceivableId, CustomerId, InvoiceReference
from ...domain.receivable.receivable import Receivable
from ...domain.receivable.repositories import IReceivableRepository, ICollectionActionRepository
from ...domain.receivable.dunning import DunningStage
from ...domain.payment.value_objects import PaymentId, PaymentAllocationId
from ...domain.payment.repositories import IPaymentAllocationRepository
from ...presentation.api.dependencies.context import ExecutionContext
from .commands import (
    CreateReceivableCommand,
    ApplyPaymentAllocationCommand,
    AllocatePaymentCommand,
    MarkReceivableOverdueCommand,
    SettleReceivableCommand,
    WriteOffReceivableCommand,
    AdvanceDunningStageCommand,
    UpdateReceivableAgingCommand
)
from .errors import ReceivableErrors


class CreateReceivableHandler:
    """
    Handler to create a new Receivable aggregate when an Invoice is issued.
    Idempotent: returns the existing receivable if already created.
    """
    def __init__(self, receivable_repo: IReceivableRepository, invoice_repo: IInvoiceRepository):
        self._receivable_repo = receivable_repo
        self._invoice_repo = invoice_repo

    async def handle(self, command: CreateReceivableCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Idempotency check: check if receivable already exists for this invoice
        existing = await self._receivable_repo.find_by_invoice_id(InvoiceId(command.invoice_id))
        if existing:
            # Enforce org boundary validation
            if existing.organization_id.value != ctx.organization_id:
                return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))
            return Result.ok(existing)

        # Load Invoice to get latest commercial details
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(ResultError.not_found("Invoice not found."))

        # Enforce invoice organization mismatch validation
        if invoice.organization_id.value != command.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Invoice does not belong to the command's organization."))

        # Construct Receivable aggregate
        rec_res = Receivable.create(
            id=ReceivableId(command.receivable_id),
            organization_id=OrganizationId(command.organization_id),
            customer_id=CustomerId(invoice.customer_id.value) if invoice.customer_id else CustomerId(str(uuid4())),
            invoice_id=InvoiceId(command.invoice_id),
            original_amount=invoice.totals.grand_total,
            due_date=invoice.due_at,
            invoice_reference=InvoiceReference(invoice.invoice_number.value) if invoice.invoice_number else None
        )
        if rec_res.is_failure:
            return Result.fail(rec_res.error)

        receivable = rec_res.value
        await self._receivable_repo.save(receivable)
        return Result.ok(receivable)


class ApplyPaymentAllocationHandler:
    """
    Handler to apply a payment allocation to a Receivable.
    Uses pessimistic locking (FOR UPDATE) and checks allocation status for idempotency.
    """
    def __init__(
        self,
        receivable_repo: IReceivableRepository,
        allocation_repo: IPaymentAllocationRepository
    ):
        self._receivable_repo = receivable_repo
        self._allocation_repo = allocation_repo

    async def handle(self, command: ApplyPaymentAllocationCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Fetch payment allocation to check idempotency status
        allocation = await self._allocation_repo.find_by_id(PaymentAllocationId(command.allocation_id))
        if not allocation:
            return Result.fail(ResultError.not_found("Payment allocation not found."))

        if allocation.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Allocation belongs to another organization."))

        # Pessimistic row locking on target Receivable
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Tenant ownership validation on the aggregate
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        # Idempotency Check: if status is already APPLIED/PROCESSED, return ok
        if allocation.status == "APPLIED":
            return Result.ok(receivable)

        # Convert input amount to Money object
        currency_type = CurrencyType.DIGITAL_ASSET if command.currency_code in ("USDC", "USDT") else CurrencyType.FIAT
        currency = Currency(command.currency_code, currency_type)
        allocation_money = Money(command.allocated_amount, currency)

        # Apply allocation inside the domain model
        apply_res = receivable.apply_payment_allocation(allocation_money)
        if apply_res.is_failure:
            return Result.fail(apply_res.error)

        # Update allocation status to mark it processed
        allocation._props["status"] = "APPLIED"
        allocation._props["updated_at"] = Clock.now()

        # Save both entities within the active transaction context
        await self._allocation_repo.save(allocation)
        await self._receivable_repo.save(receivable)

        return Result.ok(receivable)


class AllocatePaymentHandler:
    """
    Handler to allocate a payment directly to a Receivable, enforcing tenant isolation, dunning limits,
    write-off rules, and duplicate checks.
    """
    def __init__(
        self,
        receivable_repo: IReceivableRepository,
        allocation_repo: IPaymentAllocationRepository,
        collection_action_repo: ICollectionActionRepository
    ):
        self._receivable_repo = receivable_repo
        self._allocation_repo = allocation_repo
        self._collection_action_repo = collection_action_repo

    async def handle(self, command: AllocatePaymentCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Fetch payment allocation to check idempotency status
        allocation = await self._allocation_repo.find_by_id(PaymentAllocationId(command.allocation_reference))
        if not allocation:
            return Result.fail(ReceivableErrors.payment_not_found("Payment allocation not found."))

        if allocation.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Allocation belongs to another organization."))

        # Pessimistic row locking on target Receivable
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Tenant ownership validation on the aggregate
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        # Check if receivable is already settled or written off
        if receivable.status == "SETTLED":
            return Result.fail(ReceivableErrors.receivable_already_settled())
        if receivable.status == "WRITTEN_OFF":
            return Result.fail(ReceivableErrors.receivable_already_written_off())

        # Idempotency Check: if status is already APPLIED, return ok
        if allocation.status == "APPLIED":
            return Result.ok(receivable)

        # Convert input amount to Money object
        currency_type = CurrencyType.DIGITAL_ASSET if command.currency_code in ("USDC", "USDT") else CurrencyType.FIAT
        currency = Currency(command.currency_code, currency_type)
        allocation_money = Money(command.allocated_amount, currency)

        # Apply allocation inside the domain model
        apply_res = receivable.allocate_payment(
            payment_id=command.payment_id,
            amount=allocation_money,
            allocation_reference=command.allocation_reference
        )
        if apply_res.is_failure:
            # Map specific domain validation failures to ResultError
            if "exceeds outstanding balance" in apply_res.error.message:
                return Result.fail(ReceivableErrors.allocation_exceeds_outstanding(apply_res.error.message))
            return Result.fail(ReceivableErrors.payment_allocation_invalid(apply_res.error.message))

        # Update allocation status to mark it processed
        allocation._props["status"] = "APPLIED"
        allocation._props["updated_at"] = Clock.now()

        # Save both entities within the active transaction context
        await self._allocation_repo.save(allocation)
        await self._receivable_repo.save(receivable)

        if receivable.status == "SETTLED":
            await self._collection_action_repo.cancel_pending_actions(receivable.id)

        return Result.ok(receivable)


class MarkReceivableOverdueHandler:
    """
    Handler to mark a receivable overdue.
    """
    def __init__(self, receivable_repo: IReceivableRepository):
        self._receivable_repo = receivable_repo

    async def handle(self, command: MarkReceivableOverdueCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Load receivable aggregate with locking
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Validate owner
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        # Execute check overdue transition
        receivable.check_overdue(Clock.now())

        # Save
        await self._receivable_repo.save(receivable)
        return Result.ok(receivable)


class SettleReceivableHandler:
    """
    Handler to settle a receivable, verifying that the outstanding amount is fully paid.
    """
    def __init__(self, receivable_repo: IReceivableRepository, collection_action_repo: ICollectionActionRepository):
        self._receivable_repo = receivable_repo
        self._collection_action_repo = collection_action_repo

    async def handle(self, command: SettleReceivableCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Load receivable aggregate with locking
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Validate owner
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        if receivable.status == "SETTLED":
            return Result.ok(receivable)

        if receivable.status == "WRITTEN_OFF":
            return Result.fail(ReceivableErrors.receivable_already_written_off())

        # Settle invariants
        if receivable.outstanding_amount.amount > Decimal("0.00"):
            return Result.fail(ReceivableErrors.invalid_receivable_state("Cannot settle receivable with outstanding balance."))

        # Mark settled
        receivable._props["status"] = "SETTLED"
        receivable._props["updated_at"] = Clock.now()

        # Save
        await self._receivable_repo.save(receivable)
        await self._collection_action_repo.cancel_pending_actions(receivable.id)
        return Result.ok(receivable)


class WriteOffReceivableHandler:
    """
    Handler to write off a receivable, enforcing organizational rules and preventing post-write-off mutations.
    """
    def __init__(self, receivable_repo: IReceivableRepository, collection_action_repo: ICollectionActionRepository):
        self._receivable_repo = receivable_repo
        self._collection_action_repo = collection_action_repo

    async def handle(self, command: WriteOffReceivableCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Load receivable aggregate with locking
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Validate owner
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        if receivable.status == "WRITTEN_OFF":
            return Result.ok(receivable)

        if receivable.status == "SETTLED":
            return Result.fail(ReceivableErrors.invalid_receivable_state("Cannot write off settled receivable."))

        # Write-off operation
        write_res = receivable.write_off(command.reason, Clock.now())
        if write_res.is_failure:
            return Result.fail(write_res.error)

        # Save
        await self._receivable_repo.save(receivable)
        await self._collection_action_repo.cancel_pending_actions(receivable.id)
        return Result.ok(receivable)


class AdvanceDunningStageHandler:
    """
    Handler to advance dunning stage, emitting domain events and schedules.
    """
    def __init__(self, receivable_repo: IReceivableRepository):
        self._receivable_repo = receivable_repo

    async def handle(self, command: AdvanceDunningStageCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Load receivable aggregate with locking
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Validate owner
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        if receivable.status == "SETTLED":
            return Result.fail(ReceivableErrors.receivable_already_settled())

        if receivable.status == "WRITTEN_OFF":
            return Result.fail(ReceivableErrors.receivable_already_written_off())

        try:
            next_stage_enum = DunningStage(command.next_stage)
        except ValueError:
            return Result.fail(ReceivableErrors.invalid_dunning_transition(f"Invalid dunning stage: {command.next_stage}"))

        # Execute dunning stage change
        try:
            adv_res = receivable.advance_dunning_stage(
                next_stage_enum,
                occurred_on=Clock.now(),
                next_attempt_at=command.next_attempt_at
            )
            if adv_res.is_failure:
                return Result.fail(ReceivableErrors.invalid_dunning_transition(adv_res.error.message))
        except ValueError as err:
            return Result.fail(ReceivableErrors.invalid_dunning_transition(str(err)))

        # Save
        await self._receivable_repo.save(receivable)
        return Result.ok(receivable)


class UpdateReceivableAgingHandler:
    """
    Handler to update a receivable's aging calculation and detect thresholds.
    """
    def __init__(self, receivable_repo: IReceivableRepository):
        self._receivable_repo = receivable_repo

    async def handle(self, command: UpdateReceivableAgingCommand, ctx: ExecutionContext) -> Result[Receivable]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Context tenant mismatch."))

        # Load receivable aggregate with locking
        receivable = await self._receivable_repo.get_by_id(ReceivableId(command.receivable_id), lock=True)
        if not receivable:
            return Result.fail(ReceivableErrors.receivable_not_found())

        # Validate owner
        if receivable.organization_id.value != ctx.organization_id:
            return Result.fail(ReceivableErrors.unauthorized_organization_access("Receivable belongs to another organization."))

        # Call domain logic
        receivable.update_aging_and_detect_threshold(Clock)

        # Save
        await self._receivable_repo.save(receivable)
        return Result.ok(receivable)
