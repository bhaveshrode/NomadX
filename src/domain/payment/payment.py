from datetime import datetime
from enum import Enum
from typing import Optional

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from ..invoice.value_objects import Money
from .value_objects import (
    PaymentId,
    PaymentIntentId,
    PaymentAsset,
    PaymentNetwork
)
from .events import (
    PaymentDetected,
    PaymentVerificationStarted,
    PaymentConfirmed,
    PaymentRejected
)


class PaymentStatus(str, Enum):
    DETECTED = "DETECTED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ReconciliationStatus(str, Enum):
    UNRECONCILED = "UNRECONCILED"
    PARTIALLY_ALLOCATED = "PARTIALLY_ALLOCATED"
    FULLY_ALLOCATED = "FULLY_ALLOCATED"
    OVERPAYMENT = "OVERPAYMENT"
    EXCEPTION = "EXCEPTION"


class Payment(AggregateRoot[PaymentId]):
    """
    Aggregate root representing an actual payment transaction observed on the network.
    """
    def __init__(self, id: PaymentId, props: dict):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def payment_intent_id(self) -> Optional[PaymentIntentId]:
        return self._props.get("payment_intent_id")

    @property
    def observed_amount(self) -> Money:
        return self._props["observed_amount"]

    @property
    def asset(self) -> PaymentAsset:
        return self._props["asset"]

    @property
    def network(self) -> PaymentNetwork:
        return self._props["network"]

    @property
    def transaction_reference(self) -> str:
        return self._props["transaction_reference"]

    @property
    def sender(self) -> str:
        return self._props["sender"]

    @property
    def receiver(self) -> str:
        return self._props["receiver"]

    @property
    def status(self) -> PaymentStatus:
        return self._props["status"]

    @property
    def reconciliation_status(self) -> ReconciliationStatus:
        return self._props.get("reconciliation_status", ReconciliationStatus.UNRECONCILED)

    @property
    def observed_at(self) -> datetime:
        return self._props["observed_at"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: PaymentId,
        organization_id: OrganizationId,
        payment_intent_id: Optional[PaymentIntentId],
        observed_amount: Money,
        asset: PaymentAsset,
        network: PaymentNetwork,
        transaction_reference: str,
        sender: str,
        receiver: str,
        observed_at: datetime
    ) -> Result["Payment"]:
        """
        Factory method to construct a new observed Payment in DETECTED state.
        """
        if None in (id, organization_id, observed_amount, asset, network, transaction_reference, sender, receiver, observed_at):
            return Result.fail(ResultError.validation("All fields except payment_intent_id are mandatory and cannot be null."))

        if observed_amount.amount <= 0:
            return Result.fail(ResultError.validation("Observed payment amount must be positive."))

        # Enforce consistency: observed_amount currency code must match asset code
        if observed_amount.currency.code != asset.code:
            return Result.fail(ResultError.validation(
                f"Observed amount currency '{observed_amount.currency.code}' does not match asset code '{asset.code}'."
            ))

        now = Clock.now()

        props = {
            "organization_id": organization_id,
            "payment_intent_id": payment_intent_id,
            "observed_amount": observed_amount,
            "asset": asset,
            "network": network,
            "transaction_reference": transaction_reference.strip(),
            "sender": sender.strip(),
            "receiver": receiver.strip(),
            "status": PaymentStatus.DETECTED,
            "reconciliation_status": ReconciliationStatus.UNRECONCILED,
            "observed_at": observed_at,
            "created_at": now,
            "updated_at": now
        }

        instance = cls(id, props)
        instance._add_domain_event(PaymentDetected(id, now))
        return Result.ok(instance)

    def update_reconciliation_status(self, status: ReconciliationStatus) -> None:
        """Updates the reconciliation state of this Payment."""
        self._props["reconciliation_status"] = status
        self._props["updated_at"] = Clock.now()

    def start_verification(self) -> Result[None]:
        """
        Transitions status from DETECTED to PENDING_VERIFICATION.
        """
        if self.status != PaymentStatus.DETECTED:
            return Result.fail(ResultError.conflict(f"Cannot start verification from status '{self.status}'."))

        self._props["status"] = PaymentStatus.PENDING_VERIFICATION
        self._props["updated_at"] = Clock.now()
        self._add_domain_event(PaymentVerificationStarted(self.id, self.updated_at))
        return Result.ok()

    def confirm(self) -> Result[None]:
        """
        Transitions status to CONFIRMED. Allowed from DETECTED or PENDING_VERIFICATION.
        """
        if self.status not in (PaymentStatus.DETECTED, PaymentStatus.PENDING_VERIFICATION):
            return Result.fail(ResultError.conflict(f"Cannot confirm payment from status '{self.status}'."))

        self._props["status"] = PaymentStatus.CONFIRMED
        self._props["updated_at"] = Clock.now()
        self._add_domain_event(PaymentConfirmed(self.id, self.updated_at))
        return Result.ok()

    def reject(self) -> Result[None]:
        """
        Transitions status to REJECTED. Allowed from DETECTED or PENDING_VERIFICATION.
        """
        if self.status not in (PaymentStatus.DETECTED, PaymentStatus.PENDING_VERIFICATION):
            return Result.fail(ResultError.conflict(f"Cannot reject payment from status '{self.status}'."))

        self._props["status"] = PaymentStatus.REJECTED
        self._props["updated_at"] = Clock.now()
        self._add_domain_event(PaymentRejected(self.id, self.updated_at))
        return Result.ok()
