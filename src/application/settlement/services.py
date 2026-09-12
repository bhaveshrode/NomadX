from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from ...domain.organization.value_objects import OrganizationId
from ...domain.payment.repositories import IPaymentRepository
from ...domain.payment.value_objects import PaymentId
from ...domain.settlement.repositories import ISettlementRepository
from ...domain.settlement.settlement import SettlementStatus


@dataclass(frozen=True)
class SettlementAvailability:
    payment_amount: Decimal
    already_settled: Decimal
    available_amount: Decimal
    currency: str


class ISettlementAvailabilityService(ABC):
    """
    Interface defining operations for calculating the available funds of a Payment
    aggregate for settlement, isolating aggregate loading from global listings.
    """
    @abstractmethod
    async def get_availability(self, organization_id: OrganizationId, payment_id: str) -> SettlementAvailability:
        """Calculates and returns the SettlementAvailability representation for a payment."""
        pass


class SettlementAvailabilityService(ISettlementAvailabilityService):
    """
    SQLAlchemy/Repository-backed service to compute available funds for settlement.
    """
    def __init__(self, settlement_repo: ISettlementRepository, payment_repo: IPaymentRepository):
        self._settlement_repo = settlement_repo
        self._payment_repo = payment_repo

    async def get_availability(self, organization_id: OrganizationId, payment_id: str) -> SettlementAvailability:
        payment = await self._payment_repo.find_by_id(PaymentId(payment_id))
        if not payment:
            raise ValueError("Payment not found.")

        if payment.organization_id.value != organization_id.value:
            raise ValueError("Payment ownership mismatch.")

        settlements = await self._settlement_repo.find_by_payment_id(payment_id)

        already_settled = Decimal("0.00")
        for s in settlements:
            if s.organization_id.value == organization_id.value:
                # Exclude cancelled and failed (excluding retryable/retrying)
                if s.status not in (SettlementStatus.CANCELLED, SettlementStatus.FAILED):
                    already_settled += s.amount.amount

        payment_amount = payment.observed_amount.amount
        available_amount = payment_amount - already_settled
        if available_amount < 0:
            available_amount = Decimal("0.00")

        return SettlementAvailability(
            payment_amount=payment_amount,
            already_settled=already_settled,
            available_amount=available_amount,
            currency=payment.observed_amount.currency.code
        )
