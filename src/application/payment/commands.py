from datetime import datetime
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from ...domain.shared.result.result import Result, ResultError
from ...domain.organization.value_objects import OrganizationId
from ...domain.invoice.value_objects import InvoiceId, Money, Currency, CurrencyType
from ...domain.payment.value_objects import (
    PaymentIntentId,
    PaymentId,
    PaymentReference,
    PaymentAsset,
    PaymentNetwork,
    PaymentDestination,
    PaymentMethod,
    PaymentAllocationId
)
from ...domain.payment.payment_intent import PaymentIntent, PaymentIntentStatus
from ...domain.payment.payment import Payment, PaymentStatus, ReconciliationStatus
from ...domain.payment.allocation import PaymentAllocation
from ...domain.payment.repositories import IPaymentIntentRepository, IPaymentRepository, IPaymentAllocationRepository
from ...domain.invoice.repositories import IInvoiceRepository
from ...presentation.api.dependencies.context import ExecutionContext
from .errors import PaymentErrors
from .verification import PaymentVerificationEvidence, PaymentVerificationService, BlockchainClientProvider


# --- Payment Intent Commands & Handlers ---

@dataclass(frozen=True)
class CreatePaymentIntentCommand:
    payment_intent_id: str
    invoice_id: str
    payment_reference: str
    requested_amount: Decimal
    requested_asset_code: str
    requested_asset_type: str
    requested_asset_decimals: int
    destination_type: str
    destination_identifier: str
    destination_network: Optional[str]
    payment_method: str
    expires_at: datetime
    organization_id: str


class CreatePaymentIntentHandler:
    def __init__(self, invoice_repo: IInvoiceRepository, intent_repo: IPaymentIntentRepository):
        self._invoice_repo = invoice_repo
        self._intent_repo = intent_repo

    async def handle(self, command: CreatePaymentIntentCommand, ctx: ExecutionContext) -> Result[PaymentIntent]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access("Context tenant mismatch."))

        # Load Invoice
        invoice = await self._invoice_repo.find_by_id(InvoiceId(command.invoice_id))
        if not invoice:
            return Result.fail(ResultError.not_found("Invoice not found."))

        # Enforce invoice ownership validation
        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.invoice_ownership_mismatch())

        # Construct aggregate objects
        currency_type = CurrencyType.DIGITAL_ASSET if command.requested_asset_type == "DIGITAL_ASSET" else CurrencyType.FIAT
        currency = Currency(command.requested_asset_code, currency_type)
        money = Money(command.requested_amount, currency)

        asset = PaymentAsset(command.requested_asset_code, command.requested_asset_type, command.requested_asset_decimals)
        destination = PaymentDestination(command.destination_type, command.destination_identifier, command.destination_network)
        
        intent_id = PaymentIntentId(command.payment_intent_id)
        
        # Check reference duplicate
        existing = await self._intent_repo.find_by_reference(PaymentReference(command.payment_reference))
        if existing:
            return Result.fail(ResultError.conflict("Payment intent reference already exists."))

        intent_res = PaymentIntent.create(
            id=intent_id,
            organization_id=OrganizationId(command.organization_id),
            invoice_id=InvoiceId(command.invoice_id),
            reference=PaymentReference(command.payment_reference),
            requested_amount=money,
            requested_asset=asset,
            destination=destination,
            payment_method=PaymentMethod(command.payment_method),
            expires_at=command.expires_at
        )

        if intent_res.is_failure:
            return Result.fail(intent_res.error)

        intent = intent_res.value
        await self._intent_repo.save(intent)
        return Result.ok(intent)


@dataclass(frozen=True)
class ActivatePaymentIntentCommand:
    payment_intent_id: str


class ActivatePaymentIntentHandler:
    def __init__(self, intent_repo: IPaymentIntentRepository):
        self._intent_repo = intent_repo

    async def handle(self, command: ActivatePaymentIntentCommand, ctx: ExecutionContext) -> Result[None]:
        intent = await self._intent_repo.find_by_id(PaymentIntentId(command.payment_intent_id))
        if not intent:
            return Result.fail(PaymentErrors.payment_intent_not_found())

        # Tenant Guard
        if intent.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        act_res = intent.activate()
        if act_res.is_failure:
            return act_res

        await self._intent_repo.save(intent)
        return Result.ok()


@dataclass(frozen=True)
class CancelPaymentIntentCommand:
    payment_intent_id: str


class CancelPaymentIntentHandler:
    def __init__(self, intent_repo: IPaymentIntentRepository):
        self._intent_repo = intent_repo

    async def handle(self, command: CancelPaymentIntentCommand, ctx: ExecutionContext) -> Result[None]:
        intent = await self._intent_repo.find_by_id(PaymentIntentId(command.payment_intent_id))
        if not intent:
            return Result.fail(PaymentErrors.payment_intent_not_found())

        # Tenant Guard
        if intent.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        canc_res = intent.cancel()
        if canc_res.is_failure:
            return canc_res

        await self._intent_repo.save(intent)
        return Result.ok()


@dataclass(frozen=True)
class ExpirePaymentIntentCommand:
    payment_intent_id: str


class ExpirePaymentIntentHandler:
    def __init__(self, intent_repo: IPaymentIntentRepository):
        self._intent_repo = intent_repo

    async def handle(self, command: ExpirePaymentIntentCommand, ctx: ExecutionContext) -> Result[None]:
        intent = await self._intent_repo.find_by_id(PaymentIntentId(command.payment_intent_id))
        if not intent:
            return Result.fail(PaymentErrors.payment_intent_not_found())

        # Tenant Guard
        if intent.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        exp_res = intent.expire()
        if exp_res.is_failure:
            return exp_res

        await self._intent_repo.save(intent)
        return Result.ok()


# --- Payment Commands & Handlers ---

@dataclass(frozen=True)
class DetectPaymentCommand:
    payment_id: str
    payment_intent_id: Optional[str]
    observed_amount: Decimal
    asset_code: str
    asset_type: str
    asset_decimals: int
    network_code: str
    network_type: str
    transaction_reference: str
    sender: str
    receiver: str
    observed_at: datetime
    organization_id: str


class DetectPaymentHandler:
    def __init__(self, payment_repo: IPaymentRepository, intent_repo: IPaymentIntentRepository):
        self._payment_repo = payment_repo
        self._intent_repo = intent_repo

    async def handle(self, command: DetectPaymentCommand, ctx: ExecutionContext) -> Result[Payment]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access("Context tenant mismatch."))

        # Enforce intent checks if provided
        intent_id = None
        if command.payment_intent_id:
            intent = await self._intent_repo.find_by_id(PaymentIntentId(command.payment_intent_id))
            if not intent:
                return Result.fail(PaymentErrors.payment_intent_not_found())
            if intent.organization_id.value != ctx.organization_id:
                return Result.fail(PaymentErrors.unauthorized_access("Payment intent belongs to another organization."))
            intent_id = intent.id

        # Unique index check to prevent duplicate ingestion
        network = PaymentNetwork(command.network_code, command.network_type)
        existing = await self._payment_repo.find_by_transaction_reference(network, command.transaction_reference)
        if existing:
            return Result.fail(PaymentErrors.duplicate_payment())

        # Reconstruct aggregates
        currency_type = CurrencyType.DIGITAL_ASSET if command.asset_type == "DIGITAL_ASSET" else CurrencyType.FIAT
        currency = Currency(command.asset_code, currency_type)
        money = Money(command.observed_amount, currency)
        
        asset = PaymentAsset(command.asset_code, command.asset_type, command.asset_decimals)
        
        payment_res = Payment.create(
            id=PaymentId(command.payment_id),
            organization_id=OrganizationId(command.organization_id),
            payment_intent_id=intent_id,
            observed_amount=money,
            asset=asset,
            network=network,
            transaction_reference=command.transaction_reference,
            sender=command.sender,
            receiver=command.receiver,
            observed_at=command.observed_at
        )

        if payment_res.is_failure:
            return Result.fail(payment_res.error)

        payment = payment_res.value
        try:
            await self._payment_repo.save(payment)
        except ValueError as e:
            if str(e) == "PAYMENT_ALREADY_EXISTS":
                return Result.fail(PaymentErrors.duplicate_payment())
            raise e

        return Result.ok(payment)


@dataclass(frozen=True)
class StartPaymentVerificationCommand:
    payment_id: str


class StartPaymentVerificationHandler:
    def __init__(
        self,
        payment_repo: IPaymentRepository,
        intent_repo: IPaymentIntentRepository,
        client_provider: BlockchainClientProvider,
        verification_service: PaymentVerificationService
    ):
        self._payment_repo = payment_repo
        self._intent_repo = intent_repo
        self._client_provider = client_provider
        self._verification_service = verification_service

    async def handle(self, command: StartPaymentVerificationCommand, ctx: ExecutionContext) -> Result[None]:
        payment = await self._payment_repo.find_by_id(PaymentId(command.payment_id))
        if not payment:
            return Result.fail(PaymentErrors.payment_not_found())

        # Tenant Guard
        if payment.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        # Load intent if linked
        intent = None
        if payment.payment_intent_id:
            intent = await self._intent_repo.find_by_id(payment.payment_intent_id)
            if not intent:
                return Result.fail(PaymentErrors.payment_intent_not_found())
            # Cross-tenant check
            if intent.organization_id.value != ctx.organization_id:
                return Result.fail(PaymentErrors.unauthorized_access())

        # Transition payment status to PENDING_VERIFICATION if it was DETECTED
        if payment.status == PaymentStatus.DETECTED:
            verify_res = payment.start_verification()
            if verify_res.is_failure:
                return verify_res
            await self._payment_repo.save(payment)

        # Retrieve on-chain details
        try:
            client = self._client_provider.get_client(payment.network.code)
            receipt = await client.get_transaction_receipt(payment.transaction_reference)
            current_block = await client.get_latest_block_number()
        except Exception as e:
            return Result.fail(ResultError.validation(f"Blockchain RPC client query failed: {str(e)}"))

        if not receipt:
            # Transaction reference not found on chain -> reject
            rejection_res = payment.reject()
            if rejection_res.is_failure:
                return rejection_res
            await self._payment_repo.save(payment)
            return Result.fail(ResultError.validation("Transaction receipt not found on chain."))

        # Reconstruct on-chain facts from receipt
        status_val = receipt.get("status")
        if isinstance(status_val, str):
            if status_val.startswith("0x"):
                status_int = int(status_val, 16)
            else:
                status_int = int(status_val)
        elif isinstance(status_val, int):
            status_int = status_val
        else:
            status_int = 0

        block_num_val = receipt.get("blockNumber")
        if isinstance(block_num_val, str):
            if block_num_val.startswith("0x"):
                block_number = int(block_num_val, 16)
            else:
                block_number = int(block_num_val)
        else:
            block_number = int(block_num_val)

        # Determine token contract address
        token_contract = None
        logs = receipt.get("logs")
        if logs and len(logs) > 0:
            token_contract = logs[0].get("address")

        if not token_contract:
            # Fallback lookup from asset registry matching asset code and network
            for (net, addr), (symbol, decimals) in self._verification_service._registry._registry.items():
                if net.upper() == payment.network.code.upper() and symbol.upper() == payment.asset.code.upper():
                    token_contract = addr
                    break

        if not token_contract:
            token_contract = "0xUnknownContract"

        # Construct verification evidence payload
        evidence = PaymentVerificationEvidence(
            network=payment.network.code,
            token_contract=token_contract,
            sender=payment.sender,
            receiver=payment.receiver,
            raw_amount=int(payment.observed_amount.amount * (10 ** payment.asset.decimals)),
            transaction_status=status_int,
            block_number=block_number,
            current_block_number=current_block,
            transaction_reference=payment.transaction_reference
        )

        # Evaluate verification policy
        result = self._verification_service.verify(payment, intent, evidence)

        if result.valid:
            conf_res = payment.confirm()
            if conf_res.is_failure:
                return conf_res
            await self._payment_repo.save(payment)
            return Result.ok()
        else:
            if result.reasons == ["INSUFFICIENT_CONFIRMATIONS"]:
                # Keep PENDING_VERIFICATION (do not transition to REJECTED)
                await self._payment_repo.save(payment)
                return Result.fail(ResultError.validation("Insufficient block confirmations. Remaining pending."))
            else:
                # Fatal mismatch or failure -> reject
                rej_res = payment.reject()
                if rej_res.is_failure:
                    return rej_res
                await self._payment_repo.save(payment)
                return Result.fail(ResultError.validation(f"Payment verification failed: {', '.join(result.reasons)}"))


@dataclass(frozen=True)
class ConfirmPaymentCommand:
    payment_id: str


class ConfirmPaymentHandler:
    def __init__(self, payment_repo: IPaymentRepository):
        self._payment_repo = payment_repo

    async def handle(self, command: ConfirmPaymentCommand, ctx: ExecutionContext) -> Result[None]:
        payment = await self._payment_repo.find_by_id(PaymentId(command.payment_id))
        if not payment:
            return Result.fail(PaymentErrors.payment_not_found())

        # Tenant Guard
        if payment.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        conf_res = payment.confirm()
        if conf_res.is_failure:
            return conf_res

        await self._payment_repo.save(payment)
        return Result.ok()


@dataclass(frozen=True)
class RejectPaymentCommand:
    payment_id: str


class RejectPaymentHandler:
    def __init__(self, payment_repo: IPaymentRepository):
        self._payment_repo = payment_repo

    async def handle(self, command: RejectPaymentCommand, ctx: ExecutionContext) -> Result[None]:
        payment = await self._payment_repo.find_by_id(PaymentId(command.payment_id))
        if not payment:
            return Result.fail(PaymentErrors.payment_not_found())

        # Tenant Guard
        if payment.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        rej_res = payment.reject()
        if rej_res.is_failure:
            return rej_res

        await self._payment_repo.save(payment)
        return Result.ok()


import json
from uuid import uuid4
from ...domain.shared.clock.clock import Clock

@dataclass(frozen=True)
class ReconcilePaymentCommand:
    payment_id: str
    organization_id: str


class ReconcilePaymentHandler:
    def __init__(
        self,
        payment_repo: IPaymentRepository,
        intent_repo: IPaymentIntentRepository,
        invoice_repo: IInvoiceRepository,
        allocation_repo: IPaymentAllocationRepository
    ):
        self._payment_repo = payment_repo
        self._intent_repo = intent_repo
        self._invoice_repo = invoice_repo
        self._allocation_repo = allocation_repo

    async def handle(self, command: ReconcilePaymentCommand, ctx: ExecutionContext) -> Result[dict]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access("Context tenant mismatch."))

        payment = await self._payment_repo.find_by_id(PaymentId(command.payment_id))
        if not payment:
            return Result.fail(PaymentErrors.payment_not_found())

        if payment.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        # Enforce that only CONFIRMED payments can reconcile
        if payment.status != PaymentStatus.CONFIRMED:
            return Result.fail(ResultError.conflict("Only confirmed payments can be reconciled."))

        if not payment.payment_intent_id:
            payment.update_reconciliation_status(ReconciliationStatus.EXCEPTION)
            from ...domain.payment.events import PaymentReconciliationException
            payment._add_domain_event(
                PaymentReconciliationException(payment.id, None, "MISSING_PAYMENT_INTENT", Clock.now())
            )
            await self._payment_repo.save(payment)
            return Result.fail(ResultError.conflict("Payment has no associated payment intent."))

        intent = await self._intent_repo.find_by_id(payment.payment_intent_id)
        if not intent:
            return Result.fail(PaymentErrors.payment_intent_not_found())

        if intent.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        invoice = await self._invoice_repo.find_by_id(intent.invoice_id)
        if not invoice:
            return Result.fail(ResultError.not_found("Invoice not found."))

        if invoice.organization_id.value != ctx.organization_id:
            return Result.fail(PaymentErrors.unauthorized_access())

        # Enforce late payment exception logic if intent is cancelled or expired
        if intent.status in (PaymentIntentStatus.EXPIRED, PaymentIntentStatus.CANCELLED):
            payment.update_reconciliation_status(ReconciliationStatus.EXCEPTION)
            from ...domain.payment.events import PaymentReconciliationException
            payment._add_domain_event(
                PaymentReconciliationException(payment.id, invoice.id, f"LATE_PAYMENT_INTENT_{intent.status.value}", Clock.now())
            )
            await self._payment_repo.save(payment)
            return Result.fail(ResultError.conflict(f"Cannot reconcile payment for {intent.status.value} intent."))

        # Enforce currency / asset compatibility (supporting stablecoin 1:1 equivalence)
        pay_code = payment.asset.code.upper()
        inv_code = invoice.currency.code.upper()
        is_compatible = (pay_code == inv_code) or \
                        (pay_code in ("USDC", "USDT") and inv_code == "USD") or \
                        (pay_code == "EURC" and inv_code == "EUR")

        if not is_compatible:
            payment.update_reconciliation_status(ReconciliationStatus.EXCEPTION)
            from ...domain.payment.events import PaymentReconciliationException
            payment._add_domain_event(
                PaymentReconciliationException(payment.id, invoice.id, "CURRENCY_MISMATCH", Clock.now())
            )
            await self._payment_repo.save(payment)
            return Result.fail(ResultError.conflict(f"Payment asset '{pay_code}' does not match invoice currency '{inv_code}'."))

        # IDEMPOTENCY check
        existing_allocations = await self._allocation_repo.find_by_payment_id(payment.id)
        for alloc in existing_allocations:
            if alloc.invoice_id.value == invoice.id.value:
                invoice_allocs = await self._allocation_repo.find_by_invoice_id(invoice.id)
                invoice_total = invoice.totals.grand_total.amount
                total_invoice_allocated = sum(a.allocated_amount.amount for a in invoice_allocs)
                invoice_remaining = max(Decimal("0"), invoice_total - total_invoice_allocated)
                total_payment_allocated = sum(a.allocated_amount.amount for a in existing_allocations)
                payment_remaining = max(Decimal("0"), payment.observed_amount.amount - total_payment_allocated)
                
                return Result.ok({
                    "payment_id": payment.id.value,
                    "payment_amount": str(payment.observed_amount.amount),
                    "invoice_id": invoice.id.value,
                    "invoice_total": str(invoice_total),
                    "allocated_amount": str(alloc.allocated_amount.amount),
                    "payment_remaining": str(payment_remaining),
                    "invoice_remaining": str(invoice_remaining),
                    "status": payment.reconciliation_status.value
                })

        # CONCURRENCY PESSIMISTIC LOCK: Lock allocations query for invoice
        invoice_allocs = await self._allocation_repo.find_by_invoice_id(invoice.id, lock=True)
        invoice_total = invoice.totals.grand_total.amount
        total_invoice_allocated = sum(a.allocated_amount.amount for a in invoice_allocs)
        invoice_outstanding = max(Decimal("0"), invoice_total - total_invoice_allocated)

        payment_remaining = max(Decimal("0"), payment.observed_amount.amount - sum(a.allocated_amount.amount for a in existing_allocations))

        allocatable_amount = min(payment_remaining, invoice_outstanding)

        if allocatable_amount <= 0:
            if payment_remaining > 0 and invoice_outstanding <= 0:
                payment.update_reconciliation_status(ReconciliationStatus.OVERPAYMENT)
                status_res = ReconciliationStatus.OVERPAYMENT
            else:
                payment.update_reconciliation_status(ReconciliationStatus.FULLY_ALLOCATED)
                status_res = ReconciliationStatus.FULLY_ALLOCATED
                
            await self._payment_repo.save(payment)
            return Result.ok({
                "payment_id": payment.id.value,
                "payment_amount": str(payment.observed_amount.amount),
                "invoice_id": invoice.id.value,
                "invoice_total": str(invoice_total),
                "allocated_amount": "0.00",
                "payment_remaining": str(payment_remaining),
                "invoice_remaining": str(invoice_outstanding),
                "status": status_res.value
            })

        # Create allocation
        allocation_id = PaymentAllocationId(str(uuid4()))
        allocated_money = Money(allocatable_amount, payment.observed_amount.currency)
        alloc_res = PaymentAllocation.create(
            id=allocation_id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            organization_id=payment.organization_id,
            allocated_amount=allocated_money
        )
        if alloc_res.is_failure:
            return Result.fail(alloc_res.error)

        allocation = alloc_res.value
        await self._allocation_repo.save(allocation)

        new_payment_remaining = payment_remaining - allocatable_amount
        new_invoice_outstanding = invoice_outstanding - allocatable_amount

        if new_payment_remaining > 0:
            if new_invoice_outstanding == 0:
                rec_status = ReconciliationStatus.OVERPAYMENT
            else:
                rec_status = ReconciliationStatus.PARTIALLY_ALLOCATED
        else:
            if new_invoice_outstanding == 0:
                rec_status = ReconciliationStatus.FULLY_ALLOCATED
            else:
                rec_status = ReconciliationStatus.PARTIALLY_ALLOCATED

        payment.update_reconciliation_status(rec_status)
        await self._payment_repo.save(payment)

        if new_invoice_outstanding <= 0:
            invoice.mark_as_paid()
            await self._invoice_repo.save(invoice)

        return Result.ok({
            "payment_id": payment.id.value,
            "payment_amount": str(payment.observed_amount.amount),
            "invoice_id": invoice.id.value,
            "invoice_total": str(invoice_total),
            "allocated_amount": str(allocatable_amount),
            "payment_remaining": str(new_payment_remaining),
            "invoice_remaining": str(new_invoice_outstanding),
            "status": rec_status.value
        })
