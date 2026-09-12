from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from ..invoice.value_objects import Money
from .value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAsset,
    SettlementNetwork,
    SettlementDestination,
    SettlementMethod,
    SettlementSource,
    SettlementFailure,
    SettlementIdempotencyKey
)
from .events import (
    SettlementCreated,
    SettlementProcessingStarted,
    SettlementSubmitted,
    SettlementConfirmed,
    SettlementCompleted,
    SettlementFailed,
    SettlementRetrying,
    SettlementCancelled
)


class SettlementStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class Settlement(AggregateRoot[SettlementId]):
    """
    Aggregate root representing a Settlement instruction or request.
    Enforces a strict state machine and validation specs for fund movement.
    """
    def __init__(self, id: SettlementId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def reference(self) -> SettlementReference:
        return self._props["reference"]

    @property
    def asset(self) -> SettlementAsset:
        return self._props["asset"]

    @property
    def network(self) -> SettlementNetwork:
        return self._props["network"]

    @property
    def amount(self) -> Money:
        return self._props["amount"]

    @property
    def destination(self) -> SettlementDestination:
        return self._props["destination"]

    @property
    def method(self) -> SettlementMethod:
        return self._props["method"]

    @property
    def source(self) -> SettlementSource:
        return self._props["source"]

    @property
    def idempotency_key(self) -> SettlementIdempotencyKey:
        return self._props["idempotency_key"]

    @property
    def status(self) -> SettlementStatus:
        return self._props["status"]

    @property
    def failure(self) -> Optional[SettlementFailure]:
        return self._props.get("failure")

    @property
    def transaction_reference(self) -> Optional[str]:
        return self._props.get("transaction_reference")

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def submitted_at(self) -> Optional[datetime]:
        return self._props.get("submitted_at")

    @property
    def completed_at(self) -> Optional[datetime]:
        return self._props.get("completed_at")

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @classmethod
    def create(
        cls,
        id: SettlementId,
        organization_id: OrganizationId,
        reference: SettlementReference,
        asset: SettlementAsset,
        network: SettlementNetwork,
        amount: Money,
        destination: SettlementDestination,
        method: SettlementMethod,
        source: SettlementSource,
        idempotency_key: SettlementIdempotencyKey,
        occurred_on: Optional[datetime] = None
    ) -> Result["Settlement"]:
        """Factory method to construct a new Settlement in PENDING state."""
        from .specifications import SettlementCanBeCreated

        if None in (id, organization_id, reference, asset, network, amount, destination, method, source, idempotency_key):
            return Result.fail(ResultError.validation("All fields are mandatory and cannot be null."))

        if amount.amount <= 0:
            return Result.fail(ResultError.validation("Settlement amount must be positive."))

        if amount.currency.code != asset.code:
            return Result.fail(ResultError.validation(
                f"Settlement amount currency '{amount.currency.code}' must match asset code '{asset.code}'."
            ))

        params = {
            "amount": amount,
            "method": method,
            "destination": destination
        }
        spec = SettlementCanBeCreated()
        if not spec.is_satisfied_by(params):
            return Result.fail(ResultError.validation("Settlement method and destination type are incompatible."))

        now = occurred_on or Clock.now()
        props = {
            "organization_id": organization_id,
            "reference": reference,
            "asset": asset,
            "network": network,
            "amount": amount,
            "destination": destination,
            "method": method,
            "source": source,
            "idempotency_key": idempotency_key,
            "status": SettlementStatus.PENDING,
            "created_at": now,
            "updated_at": now,
            "failure": None,
            "transaction_reference": None,
            "submitted_at": None,
            "completed_at": None
        }

        settlement = cls(id, props)
        settlement._add_domain_event(SettlementCreated(id, organization_id, now))
        return Result.ok(settlement)

    def start_processing(self) -> Result[None]:
        """Transitions settlement from PENDING or RETRYING to PROCESSING state."""
        if self.status not in (SettlementStatus.PENDING, SettlementStatus.RETRYING):
            return Result.fail(ResultError.validation(f"Cannot start processing from state {self.status}."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.PROCESSING
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingStarted(self.id, now))
        return Result.ok(None)

    def submit(self, transaction_reference: str) -> Result[None]:
        """Submits settlement to external network, recording reference transaction."""
        from .specifications import SettlementCanBeSubmitted
        spec = SettlementCanBeSubmitted()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot submit settlement in state {self.status}."))

        if not transaction_reference or not isinstance(transaction_reference, str) or transaction_reference.strip() == "":
            return Result.fail(ResultError.validation("Transaction reference cannot be empty."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.SUBMITTED
        self._props["transaction_reference"] = transaction_reference.strip()
        self._props["submitted_at"] = now
        self._props["updated_at"] = now
        self._add_domain_event(SettlementSubmitted(self.id, transaction_reference, now))
        return Result.ok(None)

    def confirm(self) -> Result[None]:
        """Confirms the transaction execution on the network rail."""
        from .specifications import SettlementCanBeConfirmed
        spec = SettlementCanBeConfirmed()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot confirm settlement in state {self.status}."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.CONFIRMED
        self._props["updated_at"] = now
        self._add_domain_event(SettlementConfirmed(self.id, now))
        return Result.ok(None)

    def complete(self) -> Result[None]:
        """Completes the settlement, transitioning to terminal state."""
        from .specifications import SettlementCanBeCompleted
        spec = SettlementCanBeCompleted()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot complete settlement in state {self.status}."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.COMPLETED
        self._props["completed_at"] = now
        self._props["updated_at"] = now
        self._add_domain_event(SettlementCompleted(self.id, now))
        return Result.ok(None)

    def fail(self, failure: SettlementFailure) -> Result[None]:
        """Fails the settlement processing, recording failure details."""
        if self.status not in (SettlementStatus.PROCESSING, SettlementStatus.SUBMITTED):
            return Result.fail(ResultError.validation(f"Cannot fail settlement in state {self.status}."))

        if not isinstance(failure, SettlementFailure):
            return Result.fail(ResultError.validation("Failure details must be a SettlementFailure object."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.FAILED
        self._props["failure"] = failure
        self._props["updated_at"] = now
        self._add_domain_event(SettlementFailed(self.id, failure, now))
        return Result.ok(None)

    def retry(self) -> Result[None]:
        """Transitions a failed settlement back to RETRYING state if retryable."""
        from .specifications import SettlementCanBeRetried
        spec = SettlementCanBeRetried()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot retry settlement in state {self.status} with current failure."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.RETRYING
        self._props["updated_at"] = now
        self._add_domain_event(SettlementRetrying(self.id, now))
        return Result.ok(None)

    def cancel(self) -> Result[None]:
        """Cancels a pending settlement request."""
        from .specifications import SettlementCanBeCancelled
        spec = SettlementCanBeCancelled()
        if not spec.is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot cancel settlement in state {self.status}."))

        now = Clock.now()
        self._props["status"] = SettlementStatus.CANCELLED
        self._props["updated_at"] = now
        self._add_domain_event(SettlementCancelled(self.id, now))
        return Result.ok(None)
