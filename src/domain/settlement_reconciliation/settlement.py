from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.entities.entity import Entity
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from .value_objects import (
    SettlementId,
    SettlementReference,
    SettlementAmount,
    SettlementCurrency,
    SettlementPeriod,
    SettlementLineReference,
    MerchantReference,
    SettlementIdempotencyKey
)
from .reconciliation import ReconciliationStatus, ReconciliationResult
from .adjustments import SettlementAdjustment
from .events import (
    SettlementCreated,
    SettlementOpened,
    SettlementEntryAdded,
    SettlementReconciliationStarted,
    SettlementReconciled,
    SettlementDiscrepancyDetected,
    SettlementCompleted,
    SettlementCancelled,
    SettlementAdjustmentApplied,
    SettlementProcessingRetried,
    SettlementProcessingReplayed,
    SettlementProcessingCancelled,
    SettlementProcessingDeadLettered,
    SettlementProcessingRecovered,
    SettlementProcessingPaused,
    SettlementProcessingResumed,
    ReconciliationRetryScheduled,
    ReconciliationDiscrepancyResolved,
    SettlementProcessingFailed
)

class SettlementStatus(str, Enum):
    """Settlement batch lifecycle states."""
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    RECONCILING = "RECONCILING"
    RECONCILED = "RECONCILED"
    COMPLETED = "COMPLETED"
    DISCREPANCY = "DISCREPANCY"
    CANCELLED = "CANCELLED"

class SettlementEntry(Entity[SettlementLineReference]):
    """Entity representing a single financial entry line item inside a Settlement batch."""
    def __init__(
        self,
        id: SettlementLineReference,
        source_transaction_reference: str,
        source_context_type: str,
        gross_amount: SettlementAmount,
        fees_amount: SettlementAmount,
        adjustments_amount: SettlementAmount,
        net_amount: SettlementAmount,
        currency: SettlementCurrency,
        reconciliation_state: ReconciliationStatus = ReconciliationStatus.PENDING,
        status: str = "OPEN"
    ):
        super().__init__(id)
        if not source_transaction_reference or not isinstance(source_transaction_reference, str) or source_transaction_reference.strip() == "":
            raise ValueError("Source transaction reference cannot be empty.")
        if not source_context_type or not isinstance(source_context_type, str) or source_context_type.strip() == "":
            raise ValueError("Source context type cannot be empty.")
        if not isinstance(gross_amount, SettlementAmount) or not isinstance(fees_amount, SettlementAmount) or not isinstance(adjustments_amount, SettlementAmount) or not isinstance(net_amount, SettlementAmount):
            raise ValueError("Amounts must be SettlementAmount value objects.")
        if not isinstance(currency, SettlementCurrency):
            raise ValueError("Currency must be a SettlementCurrency value object.")
        if not isinstance(reconciliation_state, ReconciliationStatus):
            raise ValueError("Reconciliation state must be a ReconciliationStatus enum value.")

        # Invariant check: net_amount == gross_amount - fees_amount - adjustments_amount
        expected_net = gross_amount.amount - fees_amount.amount - adjustments_amount.amount
        if net_amount.amount != expected_net:
            raise ValueError(
                f"Entry amount mismatch. Net amount ({net_amount.amount}) must equal "
                f"gross ({gross_amount.amount}) - fees ({fees_amount.amount}) - adjustments ({adjustments_amount.amount}). "
                f"Expected: {expected_net}"
            )

        self.source_transaction_reference = source_transaction_reference.strip()
        self.source_context_type = source_context_type.strip()
        self.gross_amount = gross_amount
        self.fees_amount = fees_amount
        self.adjustments_amount = adjustments_amount
        self.net_amount = net_amount
        self.currency = currency
        self.reconciliation_state = reconciliation_state
        self.status = status

class Settlement(AggregateRoot[SettlementId]):
    """
    Settlement aggregate root for grouping and reconciling merchant transactions.
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
    def merchant_reference(self) -> MerchantReference:
        return self._props["merchant_reference"]

    @property
    def period(self) -> SettlementPeriod:
        return self._props["period"]

    @property
    def currency(self) -> SettlementCurrency:
        return self._props["currency"]

    @property
    def status(self) -> SettlementStatus:
        return self._props["status"]

    @property
    def reconciliation_state(self) -> ReconciliationStatus:
        return self._props["reconciliation_state"]

    @property
    def processing_state(self) -> str:
        return self._props.get("processing_state", "PENDING")

    @property
    def attempt_count(self) -> int:
        return self._props.get("attempt_count", 0)

    @property
    def max_attempts(self) -> int:
        return self._props.get("max_attempts", 3)

    @property
    def last_error(self) -> Optional[str]:
        return self._props.get("last_error")

    @property
    def failure_classification(self) -> Optional[str]:
        return self._props.get("failure_classification")

    @property
    def next_retry_at(self) -> Optional[datetime]:
        return self._props.get("next_retry_at")

    @property
    def processing_started_at(self) -> Optional[datetime]:
        return self._props.get("processing_started_at")

    @property
    def processing_completed_at(self) -> Optional[datetime]:
        return self._props.get("processing_completed_at")

    @property
    def dead_lettered_at(self) -> Optional[datetime]:
        return self._props.get("dead_lettered_at")

    @property
    def paused(self) -> bool:
        return self._props.get("paused", False)

    @property
    def entries(self) -> List[SettlementEntry]:
        return self._props["entries"]

    @property
    def adjustments(self) -> List[SettlementAdjustment]:
        return self._props["adjustments"]

    @property
    def reconciliation_result(self) -> Optional[ReconciliationResult]:
        return self._props.get("reconciliation_result")

    @property
    def idempotency_key(self) -> SettlementIdempotencyKey:
        return self._props["idempotency_key"]

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @property
    def gross_amount(self) -> SettlementAmount:
        total = sum((entry.gross_amount.amount for entry in self.entries), Decimal("0.00"))
        return SettlementAmount(total)

    @property
    def fees_amount(self) -> SettlementAmount:
        total = sum((entry.fees_amount.amount for entry in self.entries), Decimal("0.00"))
        return SettlementAmount(total)

    @property
    def adjustments_amount(self) -> SettlementAmount:
        total_entries = sum((entry.adjustments_amount.amount for entry in self.entries), Decimal("0.00"))
        total_aggregate = sum((adj.amount.amount for adj in self.adjustments), Decimal("0.00"))
        return SettlementAmount(total_entries + total_aggregate)

    @property
    def net_amount(self) -> SettlementAmount:
        return SettlementAmount(self.gross_amount.amount - self.fees_amount.amount - self.adjustments_amount.amount)

    @classmethod
    def create(
        cls,
        id: SettlementId,
        organization_id: OrganizationId,
        reference: SettlementReference,
        merchant_reference: MerchantReference,
        period: SettlementPeriod,
        currency: SettlementCurrency,
        idempotency_key: SettlementIdempotencyKey,
        occurred_on: Optional[datetime] = None
    ) -> Result["Settlement"]:
        """Factory method to construct a new batch Settlement in DRAFT state."""
        if None in (id, organization_id, reference, merchant_reference, period, currency, idempotency_key):
            return Result.fail(ResultError.validation("All fields are mandatory and cannot be null."))

        now = occurred_on or Clock.now()
        props = {
            "organization_id": organization_id,
            "reference": reference,
            "merchant_reference": merchant_reference,
            "period": period,
            "currency": currency,
            "idempotency_key": idempotency_key,
            "status": SettlementStatus.DRAFT,
            "reconciliation_state": ReconciliationStatus.PENDING,
            "entries": [],
            "adjustments": [],
            "reconciliation_result": None,
            "created_at": now,
            "updated_at": now
        }
        settlement = cls(id, props)
        settlement._add_domain_event(SettlementCreated(id, merchant_reference, now))
        return Result.ok(settlement)

    def add_entry(self, entry: SettlementEntry, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Adds a financial entry to the settlement batch."""
        from .specifications import CanAddSettlementEntry
        spec = CanAddSettlementEntry()
        if not spec.is_satisfied_by({"settlement": self, "entry": entry}):
            return Result.fail(ResultError.validation("Cannot add this entry to the settlement."))

        now = occurred_on or Clock.now()
        self.entries.append(entry)
        self._props["updated_at"] = now
        self._add_domain_event(SettlementEntryAdded(self.id, entry.id, now))
        return Result.ok(None)

    def apply_adjustment(self, adjustment: SettlementAdjustment, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Applies a financial adjustment to the settlement aggregate."""
        from .specifications import CanApplyAdjustment
        spec = CanApplyAdjustment()
        if not spec.is_satisfied_by({"settlement": self, "adjustment": adjustment}):
            return Result.fail(ResultError.validation("Cannot apply this adjustment to the settlement."))

        now = occurred_on or Clock.now()
        self.adjustments.append(adjustment)
        self._props["updated_at"] = now
        self._add_domain_event(SettlementAdjustmentApplied(self.id, adjustment.id, now))
        return Result.ok(None)

    def open(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Opens the draft settlement batch to allow reconciliation."""
        if self.status != SettlementStatus.DRAFT:
            return Result.fail(ResultError.validation("Settlement must be in DRAFT state to open."))
        now = occurred_on or Clock.now()
        self._props["status"] = SettlementStatus.OPEN
        self._props["updated_at"] = now
        self._add_domain_event(SettlementOpened(self.id, now))
        return Result.ok(None)

    def start_reconciliation(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Triggers reconciliation processing on the settlement."""
        if self.status not in (SettlementStatus.OPEN, SettlementStatus.DISCREPANCY):
            return Result.fail(ResultError.validation("Settlement must be OPEN or DISCREPANCY to start reconciliation."))
        now = occurred_on or Clock.now()
        self._props["status"] = SettlementStatus.RECONCILING
        self._props["reconciliation_state"] = ReconciliationStatus.PENDING
        self._props["updated_at"] = now
        self._add_domain_event(SettlementReconciliationStarted(self.id, now))
        return Result.ok(None)

    def reconcile(self, result: ReconciliationResult, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Reconciles the settlement using verification results."""
        from .specifications import CanReconcileSettlement
        if not CanReconcileSettlement().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Settlement must be in RECONCILING state to reconcile."))
        if not isinstance(result, ReconciliationResult):
            return Result.fail(ResultError.validation("Invalid reconciliation result."))

        now = occurred_on or Clock.now()
        self._props["reconciliation_result"] = result

        has_unresolved = False
        for discrepancy in result.discrepancies:
            if not discrepancy.resolved:
                has_unresolved = True
                break

        if has_unresolved or result.status == ReconciliationStatus.DISCREPANCY:
            self._props["status"] = SettlementStatus.DISCREPANCY
            self._props["reconciliation_state"] = ReconciliationStatus.DISCREPANCY
            self._props["updated_at"] = now
            self._add_domain_event(SettlementDiscrepancyDetected(self.id, len(result.discrepancies), now))
        else:
            self._props["status"] = SettlementStatus.RECONCILED
            self._props["reconciliation_state"] = ReconciliationStatus.MATCHED
            for entry in self.entries:
                entry.reconciliation_state = ReconciliationStatus.MATCHED
            self._props["updated_at"] = now
            self._add_domain_event(SettlementReconciled(self.id, now))

        return Result.ok(None)

    def resolve_discrepancy(self, entry_ref: SettlementLineReference, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Allows resolving a previously logged discrepancy."""
        if not self.reconciliation_result:
            return Result.fail(ResultError.validation("No reconciliation result exists to resolve discrepancies for."))

        now = occurred_on or Clock.now()
        updated_discrepancies = []
        found = False
        for d in self.reconciliation_result.discrepancies:
            if d.entry_reference == entry_ref:
                updated_discrepancies.append(d.resolve())
                found = True
            else:
                updated_discrepancies.append(d)

        if not found:
            return Result.fail(ResultError.validation(f"Discrepancy for entry reference {entry_ref} not found."))

        new_result = ReconciliationResult(
            status=self.reconciliation_result.status,
            discrepancies=updated_discrepancies,
            verified_at=self.reconciliation_result.verified_at,
            description="Resolved discrepancy: " + self.reconciliation_result.description
        )
        self._props["reconciliation_result"] = new_result

        has_unresolved = any(not d.resolved for d in updated_discrepancies)
        if not has_unresolved:
            self._props["status"] = SettlementStatus.RECONCILED
            self._props["reconciliation_state"] = ReconciliationStatus.MATCHED
            for entry in self.entries:
                if entry.id == entry_ref:
                    entry.reconciliation_state = ReconciliationStatus.MATCHED

        self._props["updated_at"] = now
        return Result.ok(None)

    def complete(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Completes the reconciled settlement instruction."""
        from .specifications import CanCompleteSettlement, IsSettlementBalanced, IsCurrencyConsistent
        if not CanCompleteSettlement().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Settlement must be in RECONCILING/RECONCILED state with no unresolved discrepancies to complete."))
        if not IsSettlementBalanced().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Settlement is not balanced mathematically (Gross - Fees - Adjustments != Net)."))
        if not IsCurrencyConsistent().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Currency code is inconsistent across entries and adjustments."))

        now = occurred_on or Clock.now()
        self._props["status"] = SettlementStatus.COMPLETED
        self._props["processing_state"] = "COMPLETED"
        self._props["updated_at"] = now
        self._add_domain_event(SettlementCompleted(self.id, now))
        return Result.ok(None)

    def cancel(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Cancels the settlement instruction batch."""
        from .specifications import CanCancelSettlement
        if not CanCancelSettlement().is_satisfied_by(self):
            return Result.fail(ResultError.validation(f"Cannot cancel settlement in state {self.status}."))

        now = occurred_on or Clock.now()
        self._props["status"] = SettlementStatus.CANCELLED
        self._props["processing_state"] = "CANCELLED"
        self._props["updated_at"] = now
        self._add_domain_event(SettlementCancelled(self.id, now))
        return Result.ok(None)

    def pause(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Pauses automated processing of the settlement batch."""
        if self.status in (SettlementStatus.COMPLETED, SettlementStatus.CANCELLED):
            return Result.fail(ResultError.validation("Cannot pause completed or cancelled settlements."))
        now = occurred_on or Clock.now()
        self._props["paused"] = True
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingPaused(self.id, now))
        return Result.ok(None)

    def resume(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Resumes automated processing of the settlement batch."""
        if self.status in (SettlementStatus.COMPLETED, SettlementStatus.CANCELLED):
            return Result.fail(ResultError.validation("Cannot resume completed or cancelled settlements."))
        now = occurred_on or Clock.now()
        self._props["paused"] = False
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingResumed(self.id, now))
        return Result.ok(None)

    def start_processing(self, occurred_on: Optional[datetime] = None) -> None:
        """Sets state to PROCESSING at start of automation attempt."""
        now = occurred_on or Clock.now()
        self._props["processing_state"] = "PROCESSING"
        self._props["processing_started_at"] = now
        self._props["updated_at"] = now

    def complete_processing(self, occurred_on: Optional[datetime] = None) -> None:
        """Sets state to COMPLETED at success of automation attempt."""
        now = occurred_on or Clock.now()
        self._props["processing_state"] = "COMPLETED"
        self._props["processing_completed_at"] = now
        self._props["updated_at"] = now

    def fail_processing(self, error: str, is_transient: bool, occurred_on: Optional[datetime] = None) -> None:
        """Registers a failure and manages state transitions / retries."""
        from datetime import timedelta
        now = occurred_on or Clock.now()
        attempts = self.attempt_count + 1
        self._props["attempt_count"] = attempts
        self._props["last_error"] = error
        self._props["updated_at"] = now

        if is_transient:
            self._props["failure_classification"] = "TRANSIENT"
            if attempts >= self.max_attempts:
                self._props["processing_state"] = "DEAD_LETTERED"
                self._props["dead_lettered_at"] = now
                self._add_domain_event(SettlementProcessingDeadLettered(self.id, error, now))
            else:
                self._props["processing_state"] = "RETRYING"
                minutes = 1 if attempts == 1 else (5 if attempts == 2 else 15)
                next_retry = now + timedelta(minutes=minutes)
                self._props["next_retry_at"] = next_retry
                self._add_domain_event(ReconciliationRetryScheduled(self.id, next_retry, now))
        else:
            self._props["failure_classification"] = "PERMANENT"
            self._props["processing_state"] = "FAILED"
            self._add_domain_event(SettlementProcessingFailed(self.id, error, now))

    def reset_retry(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Manually resets a failed or DLQ settlement for automated retry."""
        if self.processing_state not in ("FAILED", "DEAD_LETTERED", "RETRYING"):
            return Result.fail(ResultError.validation("Settlement processing is not in a failed state."))
        now = occurred_on or Clock.now()
        self._props["processing_state"] = "PENDING"
        self._props["attempt_count"] = 0
        self._props["last_error"] = None
        self._props["failure_classification"] = None
        self._props["next_retry_at"] = None
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingRetried(self.id, now))
        return Result.ok(None)

    def replay_processing(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Manually replays a settlement, creating a fresh processing attempt series."""
        now = occurred_on or Clock.now()
        self._props["processing_state"] = "PENDING"
        self._props["attempt_count"] = 0
        self._props["last_error"] = None
        self._props["failure_classification"] = None
        self._props["next_retry_at"] = None
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingReplayed(self.id, now))
        return Result.ok(None)

    def cancel_processing(self, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Cancels automated execution processing of the settlement batch."""
        if self.status == SettlementStatus.COMPLETED:
            return Result.fail(ResultError.validation("Cannot cancel a completed settlement."))
        now = occurred_on or Clock.now()
        self._props["processing_state"] = "CANCELLED"
        self._props["status"] = SettlementStatus.CANCELLED
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingCancelled(self.id, now))
        return Result.ok(None)

    def recover(self, occurred_on: Optional[datetime] = None) -> None:
        """Recovers a stuck processing execution."""
        now = occurred_on or Clock.now()
        self._props["processing_state"] = "RETRYING"
        from datetime import timedelta
        self._props["next_retry_at"] = now + timedelta(minutes=1)
        self._props["updated_at"] = now
        self._add_domain_event(SettlementProcessingRecovered(self.id, now))
