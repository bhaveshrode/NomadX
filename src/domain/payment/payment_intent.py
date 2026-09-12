from datetime import datetime
from enum import Enum
from typing import Optional

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from ..invoice.value_objects import InvoiceId, Money
from .value_objects import (
    PaymentIntentId,
    PaymentReference,
    PaymentAsset,
    PaymentDestination,
    PaymentMethod
)
from .events import (
    PaymentIntentCreated,
    PaymentIntentActivated,
    PaymentIntentExpired,
    PaymentIntentCancelled
)


class PaymentIntentStatus(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PaymentIntent(AggregateRoot[PaymentIntentId]):
    """
    Aggregate root representing a request to a customer to make a payment.
    """
    def __init__(self, id: PaymentIntentId, props: dict):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def invoice_id(self) -> InvoiceId:
        return self._props["invoice_id"]

    @property
    def reference(self) -> PaymentReference:
        return self._props["reference"]

    @property
    def requested_amount(self) -> Money:
        return self._props["requested_amount"]

    @property
    def requested_asset(self) -> PaymentAsset:
        return self._props["requested_asset"]

    @property
    def destination(self) -> PaymentDestination:
        return self._props["destination"]

    @property
    def payment_method(self) -> PaymentMethod:
        return self._props["payment_method"]

    @property
    def status(self) -> PaymentIntentStatus:
        return self._props["status"]

    @property
    def expires_at(self) -> datetime:
        return self._props["expires_at"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: PaymentIntentId,
        organization_id: OrganizationId,
        invoice_id: InvoiceId,
        reference: PaymentReference,
        requested_amount: Money,
        requested_asset: PaymentAsset,
        destination: PaymentDestination,
        payment_method: PaymentMethod,
        expires_at: datetime
    ) -> Result["PaymentIntent"]:
        """
        Factory method to construct a new draft PaymentIntent in CREATED state.
        """
        if None in (id, organization_id, invoice_id, reference, requested_amount, requested_asset, destination, payment_method, expires_at):
            return Result.fail(ResultError.validation("All fields are mandatory and cannot be null."))

        if requested_amount.amount <= 0:
            return Result.fail(ResultError.validation("Requested amount must be positive."))

        if expires_at and expires_at.tzinfo is not None:
            from datetime import timezone
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)

        now = Clock.now()
        if expires_at <= now:
            return Result.fail(ResultError.validation("Expiration time must be in the future."))

        # Enforce destination validity based on method
        if payment_method == PaymentMethod.DIGITAL_ASSET:
            if destination.type != "BLOCKCHAIN_ADDRESS" or not destination.network:
                return Result.fail(ResultError.validation("Digital asset payments require a blockchain destination address and network."))

        props = {
            "organization_id": organization_id,
            "invoice_id": invoice_id,
            "reference": reference,
            "requested_amount": requested_amount,
            "requested_asset": requested_asset,
            "destination": destination,
            "payment_method": payment_method,
            "status": PaymentIntentStatus.CREATED,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now
        }

        instance = cls(id, props)
        instance._add_domain_event(PaymentIntentCreated(id, organization_id, now))
        return Result.ok(instance)

    def activate(self) -> Result[None]:
        """
        Transitions status from CREATED to ACTIVE.
        """
        if self.status != PaymentIntentStatus.CREATED:
            return Result.fail(ResultError.conflict(f"Cannot activate PaymentIntent from status '{self.status}'."))

        # Guard: check if already expired at activation time
        if Clock.now() >= self.expires_at:
            self._props["status"] = PaymentIntentStatus.EXPIRED
            self._props["updated_at"] = Clock.now()
            self._add_domain_event(PaymentIntentExpired(self.id, self.organization_id, self.updated_at))
            return Result.fail(ResultError.conflict("Cannot activate an expired PaymentIntent."))

        self._props["status"] = PaymentIntentStatus.ACTIVE
        self._props["updated_at"] = Clock.now()
        self._add_domain_event(PaymentIntentActivated(self.id, self.organization_id, self.updated_at))
        return Result.ok()

    def expire(self) -> Result[None]:
        """
        Transitions status from ACTIVE to EXPIRED.
        """
        if self.status != PaymentIntentStatus.ACTIVE:
            return Result.fail(ResultError.conflict(f"Cannot expire PaymentIntent from status '{self.status}'."))

        self._props["status"] = PaymentIntentStatus.EXPIRED
        self._props["updated_at"] = Clock.now()
        self._add_domain_event(PaymentIntentExpired(self.id, self.organization_id, self.updated_at))
        return Result.ok()

    def cancel(self) -> Result[None]:
        """
        Transitions status from CREATED or ACTIVE to CANCELLED.
        """
        if self.status not in (PaymentIntentStatus.CREATED, PaymentIntentStatus.ACTIVE):
            return Result.fail(ResultError.conflict(f"Cannot cancel PaymentIntent from status '{self.status}'."))

        self._props["status"] = PaymentIntentStatus.CANCELLED
        self._props["updated_at"] = Clock.now()
        self._add_domain_event(PaymentIntentCancelled(self.id, self.organization_id, self.updated_at))
        return Result.ok()
