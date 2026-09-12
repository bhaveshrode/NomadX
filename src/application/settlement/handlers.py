from decimal import Decimal
from typing import Optional
from uuid import uuid4

from ...domain.organization.value_objects import OrganizationId
from ...domain.invoice.value_objects import Money, Currency, CurrencyType
from ...domain.payment.repositories import IPaymentRepository
from ...domain.payment.value_objects import PaymentId
from ...domain.payment.payment import PaymentStatus
from ...domain.settlement.settlement import Settlement, SettlementStatus
from ...domain.settlement.repositories import ISettlementRepository
from ...domain.settlement.value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestination,
    SettlementDestinationType,
    SettlementMethod,
    SettlementSource,
    SettlementSourceType,
    SettlementFailure,
    SettlementFailureCode,
    SettlementIdempotencyKey
)
from ...domain.shared.result.result import Result
from ...presentation.api.dependencies.context import ExecutionContext

from .commands import (
    CreateSettlementCommand,
    StartSettlementProcessingCommand,
    SubmitSettlementCommand,
    ConfirmSettlementCommand,
    CompleteSettlementCommand,
    FailSettlementCommand,
    RetrySettlementCommand,
    CancelSettlementCommand
)
from .errors import SettlementErrors
from .services import ISettlementAvailabilityService


class CreateSettlementHandler:
    """
    Handler to create a new Settlement instruction from a confirmed Payment.
    Idempotent: returns existing settlement if already created for this key.
    """
    def __init__(
        self,
        settlement_repo: ISettlementRepository,
        payment_repo: IPaymentRepository,
        availability_service: ISettlementAvailabilityService
    ):
        self._settlement_repo = settlement_repo
        self._payment_repo = payment_repo
        self._availability_service = availability_service

    async def handle(self, command: CreateSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        # Tenant Guard
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access("Context tenant mismatch."))

        # Idempotency lookup
        existing = await self._settlement_repo.find_by_idempotency_key(
            OrganizationId(command.organization_id),
            SettlementIdempotencyKey(command.idempotency_key)
        )
        if existing:
            if existing.organization_id.value != ctx.organization_id:
                return Result.fail(SettlementErrors.unauthorized_access("Idempotency key belongs to another tenant."))
            return Result.ok(existing)

        # Load parent payment
        payment = await self._payment_repo.find_by_id(PaymentId(command.payment_id))
        if not payment:
            return Result.fail(SettlementErrors.payment_not_found())

        # Verify payment ownership
        if payment.organization_id.value != command.organization_id:
            return Result.fail(SettlementErrors.payment_ownership_mismatch())

        # Verify payment status is eligible (CONFIRMED)
        if payment.status != PaymentStatus.CONFIRMED:
            return Result.fail(SettlementErrors.payment_not_eligible(f"Payment status is {payment.status}, expected CONFIRMED."))

        # Calculate availability
        try:
            avail = await self._availability_service.get_availability(
                OrganizationId(command.organization_id),
                command.payment_id
            )
        except Exception as e:
            return Result.fail(SettlementErrors.payment_not_found(str(e)))

        if command.amount > avail.available_amount:
            return Result.fail(SettlementErrors.insufficient_settlement_balance(
                f"Requested {command.amount} exceeds available {avail.available_amount}."
            ))

        # Enforce asset code compatibility
        if command.asset_code != payment.observed_amount.currency.code:
            return Result.fail(SettlementErrors.invalid_settlement_destination("Settlement asset code does not match parent payment currency."))

        # Reconstruct value objects
        try:
            currency_type = CurrencyType.DIGITAL_ASSET if command.asset_type == "DIGITAL_ASSET" else CurrencyType.FIAT
            currency = Currency(command.asset_code, currency_type)
            amount = Money(command.amount, currency)
            asset = SettlementAsset(command.asset_code, command.asset_type, command.asset_decimals)
            net = SettlementNetwork(command.network)
            dest = SettlementDestination(
                SettlementDestinationType(command.destination_type),
                command.destination_identifier,
                net
            )
            source = SettlementSource(SettlementSourceType.PAYMENT, command.payment_id)
            idem_key = SettlementIdempotencyKey(command.idempotency_key)
            ref = SettlementReference(command.reference)
        except ValueError as err:
            return Result.fail(SettlementErrors.invalid_settlement_destination(str(err)))

        # Create aggregate
        settlement_res = Settlement.create(
            id=SettlementId(command.settlement_id),
            organization_id=OrganizationId(command.organization_id),
            reference=ref,
            asset=asset,
            network=net,
            amount=amount,
            destination=dest,
            method=SettlementMethod(command.method),
            source=source,
            idempotency_key=idem_key
        )
        if settlement_res.is_failure:
            return Result.fail(settlement_res.error)

        settlement = settlement_res.value
        await self._settlement_repo.save(settlement)
        return Result.ok(settlement)


class StartSettlementProcessingHandler:
    """Handler to move Settlement from PENDING to PROCESSING state."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: StartSettlementProcessingCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        res = settlement.start_processing()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class SubmitSettlementHandler:
    """Handler to record external network submission of a Settlement."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: SubmitSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        res = settlement.submit(command.transaction_reference)
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class ConfirmSettlementHandler:
    """Handler to confirm transaction completion on external network."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: ConfirmSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        res = settlement.confirm()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class CompleteSettlementHandler:
    """Handler to mark Settlement completed successfully."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: CompleteSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        res = settlement.complete()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class FailSettlementHandler:
    """Handler to log execution failure details."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: FailSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        try:
            code = SettlementFailureCode(command.failure_code)
            failure = SettlementFailure(code, command.failure_message, command.failure_retryable)
        except ValueError as err:
            return Result.fail(SettlementErrors.invalid_settlement_state(str(err)))

        res = settlement.fail(failure)
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class RetrySettlementHandler:
    """Handler to retry a failed retryable Settlement."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: RetrySettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        res = settlement.retry()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)


class CancelSettlementHandler:
    """Handler to cancel a PENDING settlement."""
    def __init__(self, repo: ISettlementRepository):
        self._repo = repo

    async def handle(self, command: CancelSettlementCommand, ctx: ExecutionContext) -> Result[Settlement]:
        if command.organization_id != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        settlement = await self._repo.get_by_id(SettlementId(command.settlement_id), lock=True)
        if not settlement:
            return Result.fail(SettlementErrors.settlement_not_found())

        if settlement.organization_id.value != ctx.organization_id:
            return Result.fail(SettlementErrors.unauthorized_access())

        res = settlement.cancel()
        if res.is_failure:
            return Result.fail(res.error)

        await self._repo.save(settlement)
        return Result.ok(settlement)
