from datetime import datetime
from enum import Enum
from typing import Dict, List, Any, Optional
from decimal import Decimal
from uuid import uuid4

from ..shared.entities.aggregate_root import AggregateRoot
from ..shared.result.result import Result, ResultError
from ..shared.clock.clock import Clock
from ..organization.value_objects import OrganizationId
from ..invoice.value_objects import Money, Currency
from .value_objects import (
    ReceivableId, CustomerId, InvoiceId, ReceivableReference,
    CustomerReference, InvoiceReference, ReceivableIdempotencyKey
)
from .allocation import ReceivableAllocation
from .aging import AgingBucket
from .dunning import DunningStage, DunningState
from .events import (
    ReceivableCreated,
    ReceivablePartiallyPaid,
    ReceivableSettled,
    ReceivablePaid,
    ReceivableBecameOverdue,
    ReceivableWrittenOff,
    ReceivablePaymentAllocated,
    ReceivableAgingThresholdReached,
    DunningStageChanged,
    ReceivableCollectionAttentionRequired
)


class ReceivableStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    SETTLED = "SETTLED"
    OVERDUE = "OVERDUE"
    WRITTEN_OFF = "WRITTEN_OFF"


class ReceivablePaymentStatus(str, Enum):
    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"


class ReceivableCollectionStatus(str, Enum):
    CURRENT = "CURRENT"
    OVERDUE = "OVERDUE"
    COLLECTION_ATTENTION = "COLLECTION_ATTENTION"
    RESOLVED = "RESOLVED"


class Receivable(AggregateRoot[ReceivableId]):
    """
    Primary aggregate root representing the Accounts Receivable context.
    Enforces business rules regarding amounts allocated, dunning stage changes,
    aging classifications, and lifecycle transitions.
    """
    def __init__(self, id: ReceivableId, props: Dict[str, Any]):
        super().__init__(id)
        self._props = props

    @property
    def organization_id(self) -> OrganizationId:
        return self._props["organization_id"]

    @property
    def reference(self) -> ReceivableReference:
        val = self._props.get("reference")
        if val is None:
            val = ReceivableReference(f"AR-{str(self.id.value)[:8]}")
            self._props["reference"] = val
        return val

    @property
    def customer_id(self) -> CustomerId:
        return self._props["customer_id"]

    @property
    def customer_reference(self) -> CustomerReference:
        val = self._props.get("customer_reference")
        if val is None:
            val = CustomerReference(f"CUST-{str(self.customer_id.value)[:8]}")
            self._props["customer_reference"] = val
        return val

    @property
    def invoice_id(self) -> InvoiceId:
        return self._props["invoice_id"]

    @property
    def invoice_reference(self) -> InvoiceReference:
        val = self._props.get("invoice_reference")
        if val is None:
            val = InvoiceReference(f"INV-{str(self.invoice_id.value)[:8]}")
            self._props["invoice_reference"] = val
        return val

    @property
    def original_amount(self) -> Money:
        return self._props["original_amount"]

    @property
    def allocated_amount(self) -> Money:
        return self._props.get("allocated_amount", self._props.get("paid_amount"))

    # Alias to keep compatibility with existing code
    @property
    def paid_amount(self) -> Money:
        return self.allocated_amount

    @property
    def outstanding_amount(self) -> Money:
        return self._props["outstanding_amount"]

    @property
    def currency(self) -> Currency:
        return self.original_amount.currency

    @property
    def due_date(self) -> datetime:
        return self._props["due_date"]

    @property
    def status(self) -> ReceivableStatus:
        val = self._props.get("status")
        if val is None:
            # Derive status from legacy database fields payment_status / collection_status
            legacy_payment = self._props.get("payment_status")
            legacy_collection = self._props.get("collection_status")
            if self.outstanding_amount.amount == Decimal("0.00") or legacy_payment == "PAID":
                val = ReceivableStatus.SETTLED
            elif legacy_collection == "OVERDUE":
                val = ReceivableStatus.OVERDUE
            elif self.allocated_amount.amount > Decimal("0.00") or legacy_payment == "PARTIALLY_PAID":
                val = ReceivableStatus.PARTIALLY_PAID
            else:
                val = ReceivableStatus.OPEN
            self._props["status"] = val
        return val

    @property
    def dunning_state(self) -> DunningState:
        val = self._props.get("dunning_state")
        if val is None:
            val = DunningState.initial()
            self._props["dunning_state"] = val
        return val

    @property
    def allocations(self) -> List[ReceivableAllocation]:
        val = self._props.get("allocations")
        if val is None:
            val = []
            self._props["allocations"] = val
        return val

    @property
    def idempotency_key(self) -> ReceivableIdempotencyKey:
        val = self._props.get("idempotency_key")
        if val is None:
            val = ReceivableIdempotencyKey(str(uuid4()))
            self._props["idempotency_key"] = val
        return val

    @property
    def created_at(self) -> datetime:
        return self._props["created_at"]

    @property
    def updated_at(self) -> datetime:
        return self._props["updated_at"]

    @property
    def dunning_paused(self) -> bool:
        return self._props.get("dunning_paused", False)

    def pause_dunning(self) -> None:
        self._props["dunning_paused"] = True
        self._props["updated_at"] = Clock.now()

    def resume_dunning(self) -> None:
        self._props["dunning_paused"] = False
        self._props["updated_at"] = Clock.now()

    # Mapped property aliases to retain compatibility:
    @property
    def payment_status(self) -> ReceivablePaymentStatus:
        if self.outstanding_amount.amount == Decimal("0.00"):
            return ReceivablePaymentStatus.PAID
        elif self.allocated_amount.amount > Decimal("0.00"):
            return ReceivablePaymentStatus.PARTIALLY_PAID
        else:
            return ReceivablePaymentStatus.UNPAID

    @property
    def collection_status(self) -> ReceivableCollectionStatus:
        if self.outstanding_amount.amount == Decimal("0.00"):
            return ReceivableCollectionStatus.RESOLVED
        if self._props.get("collection_status") == ReceivableCollectionStatus.COLLECTION_ATTENTION:
            return ReceivableCollectionStatus.COLLECTION_ATTENTION
        if self.status == ReceivableStatus.OVERDUE:
            return ReceivableCollectionStatus.OVERDUE
        return self._props.get("collection_status", ReceivableCollectionStatus.CURRENT)

    @classmethod
    def create(
        cls,
        id: ReceivableId,
        organization_id: OrganizationId,
        customer_id: CustomerId,
        invoice_id: InvoiceId,
        original_amount: Money,
        due_date: datetime,
        reference: Optional[ReceivableReference] = None,
        customer_reference: Optional[CustomerReference] = None,
        invoice_reference: Optional[InvoiceReference] = None,
        idempotency_key: Optional[ReceivableIdempotencyKey] = None,
        occurred_on: Optional[datetime] = None
    ) -> Result["Receivable"]:
        """
        Factory method to construct a new Receivable in OPEN (or OVERDUE if already past due date) status.
        """
        if None in (id, organization_id, customer_id, invoice_id, original_amount, due_date):
            return Result.fail(ResultError.validation("All parameters must be provided."))

        if not isinstance(original_amount, Money):
            return Result.fail(ResultError.validation("Original amount must be an instance of Money."))

        if original_amount.amount <= Decimal("0.00"):
            return Result.fail(ResultError.validation("Original amount must be greater than zero."))

        now = occurred_on or datetime.utcnow()

        # Fallbacks for optional reference variables to preserve backwards compatibility
        final_ref = reference or ReceivableReference(f"AR-{str(uuid4())[:8]}")
        final_cust_ref = customer_reference or CustomerReference(f"CUST-{str(customer_id.value)[:8]}")
        final_inv_ref = invoice_reference or InvoiceReference(f"INV-{str(invoice_id.value)[:8]}")
        final_idem_key = idempotency_key or ReceivableIdempotencyKey(str(uuid4()))

        allocated_amount = Money(Decimal("0.00"), original_amount.currency)
        outstanding_amount = Money(original_amount.amount, original_amount.currency)

        initial_status = ReceivableStatus.OVERDUE if now > due_date else ReceivableStatus.OPEN
        initial_collection_status = (
            ReceivableCollectionStatus.OVERDUE 
            if now > due_date 
            else ReceivableCollectionStatus.CURRENT
        )

        props = {
            "organization_id": organization_id,
            "reference": final_ref,
            "customer_id": customer_id,
            "customer_reference": final_cust_ref,
            "invoice_id": invoice_id,
            "invoice_reference": final_inv_ref,
            "original_amount": original_amount,
            "allocated_amount": allocated_amount,
            "outstanding_amount": outstanding_amount,
            "due_date": due_date,
            "status": initial_status,
            "dunning_state": DunningState.initial(),
            "allocations": [],
            "idempotency_key": final_idem_key,
            "last_aging_bucket": AgingBucket.CURRENT,
            "collection_status": initial_collection_status,
            "dunning_paused": False,
            "created_at": now,
            "updated_at": now
        }

        receivable = cls(id, props)
        receivable._add_domain_event(
            ReceivableCreated(
                receivable_id=id,
                organization_id=organization_id,
                reference=final_ref,
                invoice_id=invoice_id,
                invoice_reference=final_inv_ref,
                customer_id=customer_id,
                customer_reference=final_cust_ref,
                original_amount=original_amount,
                due_date=due_date,
                occurred_on=now
            )
        )
        return Result.ok(receivable)

    def allocate_payment(
        self,
        payment_id: str,
        amount: Money,
        allocation_reference: str,
        occurred_on: Optional[datetime] = None,
        emit_allocation_event: bool = True
    ) -> Result[None]:
        """
        Applies a payment allocation, modifying the paid and outstanding balances,
        and transitions the status.
        """
        from .specifications import CanAllocatePayment
        if not CanAllocatePayment().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Receivable cannot accept payment allocations in its current state."))

        if amount is None or not isinstance(amount, Money):
            return Result.fail(ResultError.validation("Allocation amount must be a valid Money object."))

        if amount.amount <= Decimal("0.00"):
            return Result.fail(ResultError.validation("Allocation amount must be greater than zero."))

        if amount.currency != self.currency:
            return Result.fail(ResultError.validation("Allocation currency mismatch."))

        if amount.amount > self.outstanding_amount.amount:
            return Result.fail(ResultError.validation("Allocation amount exceeds outstanding balance."))

        now = occurred_on or datetime.utcnow()

        # Append ReceivableAllocation value object
        allocation = ReceivableAllocation(
            payment_id=payment_id,
            receivable_id=self.id,
            allocated_amount=amount,
            currency=self.currency,
            allocated_at=now,
            allocation_reference=allocation_reference
        )
        self.allocations.append(allocation)

        new_paid_amount = Money(self.allocated_amount.amount + amount.amount, self.currency)
        new_outstanding_amount = Money(self.outstanding_amount.amount - amount.amount, self.currency)

        self._props["allocated_amount"] = new_paid_amount
        self._props["outstanding_amount"] = new_outstanding_amount
        self._props["updated_at"] = now

        # Transition status
        if new_outstanding_amount.amount == Decimal("0.00"):
            self._props["status"] = ReceivableStatus.SETTLED
            self._props["collection_status"] = ReceivableCollectionStatus.RESOLVED
            self._add_domain_event(
                ReceivablePaid(
                    receivable_id=self.id,
                    organization_id=self.organization_id,
                    allocated_amount=amount,
                    paid_amount=new_paid_amount,
                    occurred_on=now
                )
            )
        else:
            self._props["status"] = ReceivableStatus.PARTIALLY_PAID
            # If collection status was RESOLVED (should not happen), bring it back to current/overdue
            if self.collection_status == ReceivableCollectionStatus.RESOLVED:
                now_naive = now.replace(tzinfo=None) if now.tzinfo else now
                due_naive = self.due_date.replace(tzinfo=None) if self.due_date.tzinfo else self.due_date
                self._props["collection_status"] = (
                    ReceivableCollectionStatus.OVERDUE if now_naive > due_naive 
                    else ReceivableCollectionStatus.CURRENT
                )
            self._add_domain_event(
                ReceivablePartiallyPaid(
                    receivable_id=self.id,
                    organization_id=self.organization_id,
                    allocated_amount=amount,
                    paid_amount=new_paid_amount,
                    outstanding_amount=new_outstanding_amount,
                    occurred_on=now
                )
            )

        # Emit standard payment allocation event
        if emit_allocation_event:
            self._add_domain_event(
                ReceivablePaymentAllocated(
                    receivable_id=self.id,
                    organization_id=self.organization_id,
                    payment_id=payment_id,
                    allocated_amount=amount,
                    allocation_reference=allocation_reference,
                    occurred_on=now
                )
            )

        return Result.ok()

    # Wrap the old allocation method to keep compatibility with existing tests
    def apply_payment_allocation(self, amount: Money, occurred_on: Optional[datetime] = None) -> Result[None]:
        return self.allocate_payment(
            payment_id="fake-payment-id",
            amount=amount,
            allocation_reference="fake-alloc-ref",
            occurred_on=occurred_on,
            emit_allocation_event=False
        )

    def refund_payment(
        self,
        payment_id: str,
        amount: Money,
        allocation_reference: str,
        occurred_on: Optional[datetime] = None
    ) -> Result[None]:
        """Reverses a payment allocation, adjusting balances and restoring status."""
        if self.status == ReceivableStatus.WRITTEN_OFF:
            return Result.fail(ResultError.validation("Cannot refund a written off receivable."))
        if amount is None or not isinstance(amount, Money):
            return Result.fail(ResultError.validation("Refund amount must be a valid Money object."))
        if amount.amount <= Decimal("0.00"):
            return Result.fail(ResultError.validation("Refund amount must be greater than zero."))
        if amount.currency != self.currency:
            return Result.fail(ResultError.validation("Refund currency mismatch."))
        if amount.amount > self.allocated_amount.amount:
            return Result.fail(ResultError.validation("Refund amount exceeds allocated amount."))

        now = occurred_on or datetime.utcnow()
        neg_amount = Money(-amount.amount, self.currency)
        allocation = ReceivableAllocation(
            payment_id=payment_id,
            receivable_id=self.id,
            allocated_amount=neg_amount,
            currency=self.currency,
            allocated_at=now,
            allocation_reference=allocation_reference
        )
        self.allocations.append(allocation)

        new_paid_amount = Money(self.allocated_amount.amount - amount.amount, self.currency)
        new_outstanding_amount = Money(self.outstanding_amount.amount + amount.amount, self.currency)

        self._props["allocated_amount"] = new_paid_amount
        self._props["outstanding_amount"] = new_outstanding_amount
        self._props["updated_at"] = now

        # Restore status
        if new_outstanding_amount.amount > 0:
            if self.status == ReceivableStatus.SETTLED:
                now_naive = now.replace(tzinfo=None) if now.tzinfo else now
                due_naive = self.due_date.replace(tzinfo=None) if self.due_date.tzinfo else self.due_date
                # If we've passed the due date, it's overdue
                if now_naive > due_naive:
                    self._props["status"] = ReceivableStatus.OVERDUE
                    self._props["collection_status"] = ReceivableCollectionStatus.OVERDUE
                else:
                    self._props["status"] = ReceivableStatus.PARTIALLY_PAID if new_paid_amount.amount > 0 else ReceivableStatus.OPEN
                    self._props["collection_status"] = ReceivableCollectionStatus.CURRENT

        self._add_domain_event(
            ReceivablePartiallyPaid(
                receivable_id=self.id,
                organization_id=self.organization_id,
                allocated_amount=neg_amount,
                paid_amount=new_paid_amount,
                outstanding_amount=new_outstanding_amount,
                occurred_on=now
            )
        )
        return Result.ok()

    def write_off(self, reason: str, occurred_on: Optional[datetime] = None) -> Result[None]:
        """Writes off the outstanding balance of a receivable."""
        from .specifications import CanWriteOff
        if not CanWriteOff().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Receivable cannot be written off in its current state."))

        if not reason or not isinstance(reason, str) or reason.strip() == "":
            return Result.fail(ResultError.validation("Reason for write-off must be provided."))

        now = occurred_on or datetime.utcnow()
        self._props["status"] = ReceivableStatus.WRITTEN_OFF
        self._props["updated_at"] = now
        self._add_domain_event(
            ReceivableWrittenOff(
                receivable_id=self.id,
                organization_id=self.organization_id,
                write_off_reason=reason,
                occurred_on=now
            )
        )
        return Result.ok()

    def advance_dunning_stage(
        self,
        next_stage: DunningStage,
        occurred_on: Optional[datetime] = None,
        next_attempt_at: Optional[datetime] = None
    ) -> Result[None]:
        """Advances the dunning stage and emits dunning events."""
        from .specifications import CanAdvanceDunningStage
        if not CanAdvanceDunningStage().is_satisfied_by(self):
            return Result.fail(ResultError.validation("Cannot advance dunning stage for this receivable."))

        now = occurred_on or datetime.utcnow()
        previous_stage = self.dunning_state.stage
        new_state = self.dunning_state.advance(next_stage, attempt_time=now, next_attempt_time=next_attempt_at)
        self._props["dunning_state"] = new_state
        self._props["collection_status"] = ReceivableCollectionStatus.COLLECTION_ATTENTION
        self._props["updated_at"] = now

        self._add_domain_event(
            DunningStageChanged(
                receivable_id=self.id,
                organization_id=self.organization_id,
                previous_stage=previous_stage,
                new_stage=next_stage,
                occurred_on=now
            )
        )
        return Result.ok()

    def calculate_days_overdue(self, current_time: datetime) -> int:
        """Returns the number of days overdue relative to a target timestamp."""
        ct = current_time.replace(tzinfo=None) if current_time.tzinfo else current_time
        dd = self.due_date.replace(tzinfo=None) if self.due_date.tzinfo else self.due_date
        if ct <= dd:
            return 0
        return (ct - dd).days

    def calculate_aging(self, clock: Clock) -> AgingBucket:
        """Calculates and returns the appropriate AgingBucket for this receivable."""
        days = self.calculate_days_overdue(clock.now())
        return AgingBucket.from_days_overdue(days)

    def update_aging_and_detect_threshold(self, clock: Clock) -> Optional[AgingBucket]:
        """Updates aging range bucket and triggers AgingThresholdReached events if transitioned."""
        new_bucket = self.calculate_aging(clock)
        old_bucket = self._props.get("last_aging_bucket", AgingBucket.CURRENT)
        if new_bucket != old_bucket:
            self._props["last_aging_bucket"] = new_bucket
            self._props["updated_at"] = clock.now()
            self._add_domain_event(
                ReceivableAgingThresholdReached(
                    receivable_id=self.id,
                    organization_id=self.organization_id,
                    aging_bucket=new_bucket,
                    occurred_on=clock.now()
                )
            )
        return new_bucket

    def check_overdue(self, current_time: datetime) -> None:
        """
        Transition status to OVERDUE if eligible and emits became overdue event.
        """
        from .specifications import CanMarkOverdue
        if CanMarkOverdue(current_time).is_satisfied_by(self):
            self._props["status"] = ReceivableStatus.OVERDUE
            self._props["collection_status"] = ReceivableCollectionStatus.OVERDUE
            self._props["updated_at"] = current_time
            self._add_domain_event(
                ReceivableBecameOverdue(
                    receivable_id=self.id,
                    organization_id=self.organization_id,
                    occurred_on=current_time
                )
            )

    def trigger_collection_attention(self, current_time: datetime) -> None:
        """Triggers collection attention manually or per business rules."""
        if self.outstanding_amount.amount > Decimal("0.00") and self.collection_status != ReceivableCollectionStatus.COLLECTION_ATTENTION:
            self._props["collection_status"] = ReceivableCollectionStatus.COLLECTION_ATTENTION
            self._props["updated_at"] = current_time
            self._add_domain_event(
                ReceivableCollectionAttentionRequired(
                    receivable_id=self.id,
                    organization_id=self.organization_id,
                    occurred_on=current_time
                )
            )
