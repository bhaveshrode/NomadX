from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, ForeignKey, UniqueConstraint, Index, Float
from .session import Base
from sqlalchemy.orm import relationship

class OrganizationDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of an Organization.
    """
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False)
    default_currency = Column(String(6), nullable=False)
    owner_id = Column(String(36), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a User.
    """
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    email_verified = Column(Boolean, nullable=False, default=False)
    verification_token = Column(String(100), nullable=True)
    login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class MembershipDbModel(Base):
    """
    SQLAlchemy model representing the connection between an Organization and a User.
    """
    __tablename__ = "memberships"

    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(20), nullable=False)
    joined_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(20), nullable=False, default="ACTIVE")


class OutboxEventDbModel(Base):
    """
    SQLAlchemy model representing a pending domain event stored for outbox delivery.
    """
    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True)
    event_type = Column(String(100), nullable=False)
    payload = Column(Text, nullable=False)  # JSON-serialized domain event properties
    processed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    dead_lettered_at = Column(DateTime, nullable=True)


class InvoiceDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of an Invoice.
    """
    __tablename__ = "invoices"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=True, unique=True, index=True)
    
    # Origin
    origin_type = Column(String(20), nullable=False) # NATIVE or EXTERNAL
    source_system = Column(String(100), nullable=True)
    external_invoice_id = Column(String(100), nullable=True)

    # Customer/Buyer
    customer_id = Column(String(36), nullable=True)
    billing_name = Column(String(100), nullable=True)
    billing_email = Column(String(255), nullable=True)
    billing_address = Column(Text, nullable=True)
    billing_country = Column(String(100), nullable=True)
    billing_tax_identifier = Column(String(100), nullable=True)

    # Currency and status
    currency_code = Column(String(6), nullable=False)
    currency_type = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)

    # Totals (Stored as String to preserve high-precision decimal representation across databases, including SQLite tests)
    subtotal = Column(String(50), nullable=False)
    discount_total = Column(String(50), nullable=False)
    charge_total = Column(String(50), nullable=False)
    tax_total = Column(String(50), nullable=False)
    grand_total = Column(String(50), nullable=False)

    # Payment Terms
    payment_terms_type = Column(String(20), nullable=False)
    due_at = Column(DateTime, nullable=True)

    # Dates
    issued_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class InvoiceLineItemDbModel(Base):
    """
    SQLAlchemy model representing a Line Item belonging to an Invoice.
    """
    __tablename__ = "invoice_line_items"

    id = Column(String(36), primary_key=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    description = Column(String(255), nullable=False)
    quantity = Column(String(50), nullable=False)
    unit_price = Column(String(50), nullable=False)
    discount = Column(String(50), nullable=False)
    tax = Column(String(50), nullable=False)
    currency = Column(String(6), nullable=False)
    amount = Column(String(50), nullable=False) # Stored net amount (quantity * unit_price - discount)
    position = Column(Integer, nullable=False)


class InvoiceTaxLineDbModel(Base):
    """
    SQLAlchemy model representing a Tax Line belonging to an Invoice.
    """
    __tablename__ = "invoice_tax_lines"

    id = Column(String(36), primary_key=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    rate = Column(String(50), nullable=False)
    taxable_amount = Column(String(50), nullable=False)
    amount = Column(String(50), nullable=False)


class InvoiceAdjustmentDbModel(Base):
    """
    SQLAlchemy model representing an Invoice Adjustment.
    """
    __tablename__ = "invoice_adjustments"

    id = Column(String(36), primary_key=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(20), nullable=False) # DISCOUNT or CHARGE
    description = Column(String(255), nullable=False)
    rate = Column(String(50), nullable=True)
    amount = Column(String(50), nullable=False)


class PaymentIntentDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Payment Intent.
    """
    __tablename__ = "payment_intents"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_reference = Column(String(50), nullable=False, unique=True, index=True)

    requested_amount = Column(String(50), nullable=False)
    asset_code = Column(String(20), nullable=False)
    asset_type = Column(String(20), nullable=False)
    asset_decimals = Column(Integer, nullable=False)

    network = Column(String(50), nullable=True)
    destination_type = Column(String(50), nullable=False)
    destination_identifier = Column(String(255), nullable=False)

    payment_method = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    expires_at = Column(DateTime, nullable=False)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class PaymentDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of an observed Payment.
    """
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_intent_id = Column(String(36), ForeignKey("payment_intents.id", ondelete="SET NULL"), nullable=True, index=True)

    observed_amount = Column(String(50), nullable=False)
    asset_code = Column(String(20), nullable=False)
    asset_type = Column(String(20), nullable=False)
    asset_decimals = Column(Integer, nullable=False)

    network = Column(String(50), nullable=False)
    transaction_reference = Column(String(255), nullable=False)
    sender = Column(String(255), nullable=False)
    receiver = Column(String(255), nullable=False)

    status = Column(String(20), nullable=False)
    reconciliation_status = Column(String(30), nullable=False, default="UNRECONCILED")
    observed_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("network", "transaction_reference", name="uq_payment_network_tx_ref"),
    )


class BlockchainCursorDbModel(Base):
    """
    SQLAlchemy model representing the scanning checkpoint for a blockchain network.
    """
    __tablename__ = "blockchain_cursors"

    network = Column(String(50), primary_key=True)
    last_processed_block = Column(Integer, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class PaymentAllocationDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a PaymentAllocation.
    """
    __tablename__ = "payment_allocations"

    id = Column(String(36), primary_key=True, index=True)
    payment_id = Column(String(36), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    allocated_amount = Column(String(50), nullable=False)
    currency = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id", name="uq_payment_invoice_allocation"),
    )


class ReceivableDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Receivable.
    """
    __tablename__ = "receivables"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(String(36), nullable=False, index=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)

    reference = Column(String(50), nullable=False, unique=True, index=True)
    customer_reference = Column(String(50), nullable=False)
    invoice_reference = Column(String(50), nullable=False)

    currency = Column(String(20), nullable=False)
    original_amount = Column(String(50), nullable=False)
    allocated_amount = Column(String(50), nullable=False)
    outstanding_amount = Column(String(50), nullable=False)
    due_date = Column(DateTime, nullable=False)

    status = Column(String(20), nullable=False)
    aging_bucket = Column(String(30), nullable=False)
    dunning_stage = Column(String(50), nullable=False)
    dunning_attempt_count = Column(Integer, nullable=False, default=0)
    next_dunning_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(255), nullable=False)
    dunning_paused = Column(Boolean, nullable=False, default=False, server_default="0")

    # Keep legacy columns for backward compatibility
    paid_amount = Column(String(50), nullable=False)
    payment_status = Column(String(20), nullable=False)
    collection_status = Column(String(30), nullable=False)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    allocations = relationship("ReceivableAllocationDbModel", back_populates="receivable", cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        UniqueConstraint("reference", name="uq_receivables_reference"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_receivables_org_idempotency"),
        UniqueConstraint("organization_id", "invoice_id", name="uq_organization_invoice_receivable"),
        Index("ix_receivables_org_status", "organization_id", "status"),
        Index("ix_receivables_org_due_date", "organization_id", "due_date"),
        Index("ix_receivables_org_cust_ref", "organization_id", "customer_reference"),
        Index("ix_receivables_org_inv_ref", "organization_id", "invoice_reference"),
        Index("ix_receivables_org_aging", "organization_id", "aging_bucket"),
        Index("ix_receivables_org_dunning", "organization_id", "dunning_stage"),
    )


class ReceivableAllocationDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a ReceivableAllocation.
    """
    __tablename__ = "receivable_allocations"

    id = Column(String(36), primary_key=True)
    receivable_id = Column(String(36), ForeignKey("receivables.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_reference = Column(String(100), nullable=False)
    allocated_amount = Column(String(50), nullable=False)
    currency = Column(String(20), nullable=False)
    allocated_at = Column(DateTime, nullable=False)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    receivable = relationship("ReceivableDbModel", back_populates="allocations")


class SettlementDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Settlement.
    """
    __tablename__ = "settlements"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    reference = Column(String(50), nullable=False, unique=True, index=True)
    source_payment_id = Column(String(36), nullable=False, index=True)

    # Asset specifications
    asset_code = Column(String(6), nullable=False)
    asset_type = Column(String(20), nullable=False)
    asset_decimals = Column(Integer, nullable=False)

    network = Column(String(50), nullable=False)
    destination_type = Column(String(20), nullable=False)
    destination_identifier = Column(String(255), nullable=False)
    method = Column(String(20), nullable=False)

    # Decimal-safe representation stored as string
    amount = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)

    # Failure fields
    failure_code = Column(String(50), nullable=True)
    failure_message = Column(Text, nullable=True)
    failure_retryable = Column(Boolean, nullable=True)

    # Idempotency and Transaction references
    idempotency_key = Column(String(255), nullable=False)
    transaction_reference = Column(String(255), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False)
    processing_started_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_settlements_org_idempotency"),
    )


class NotificationDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Notification.
    """
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    reference = Column(String(50), nullable=False, unique=True, index=True)
    notification_type = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False)
    recipient = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    template_id = Column(String(100), nullable=False)
    payload = Column(Text, nullable=False)  # JSON-serialized payload data
    priority = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, index=True)

    # Failure fields
    failure_code = Column(String(50), nullable=True)
    failure_message = Column(Text, nullable=True)
    failure_retryable = Column(Boolean, nullable=True)

    idempotency_key = Column(String(255), nullable=False)
    source_event_id = Column(String(36), nullable=False, index=True)
    source_event_type = Column(String(100), nullable=False)

    # Timestamps
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)

    # Scheduling & Retries
    scheduled_at = Column(DateTime, nullable=False)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    max_attempts = Column(Integer, nullable=False, default=3)
    replay_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_notifications_org_idempotency"),
        Index("ix_notifications_org_status", "organization_id", "status"),
    )


class NotificationTemplateDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a NotificationTemplate.
    """
    __tablename__ = "notification_templates"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    reference = Column(String(100), nullable=False, index=True)
    version = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "reference", "version", name="uq_templates_org_ref_version"),
        Index(
            "uq_active_template_per_org_ref",
            "organization_id",
            "reference",
            unique=True,
            postgresql_where=(status == "ACTIVE"),
            sqlite_where=(status == "ACTIVE")
        )
    )


class NotificationDeliveryAttemptDbModel(Base):
    """
    SQLAlchemy model representing a delivery attempt history log record.
    """
    __tablename__ = "notification_delivery_attempts"

    id = Column(String(36), primary_key=True, index=True)
    notification_id = Column(String(36), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    channel = Column(String(20), nullable=False)
    provider = Column(String(50), nullable=False)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=False)
    is_success = Column(Boolean, nullable=False)
    provider_reference = Column(String(255), nullable=True)
    failure_code = Column(String(50), nullable=True)
    failure_message = Column(Text, nullable=True)


class NotificationPreferenceDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of organization notification preferences.
    """
    __tablename__ = "notification_preferences"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    channel = Column(String(20), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "event_type", "channel", name="uq_pref_org_event_channel"),
    )


class ProviderStateDbModel(Base):
    """
    SQLAlchemy model representing a delivery provider's circuit state and metrics.
    """
    __tablename__ = "notification_provider_states"

    provider_name = Column(String(50), primary_key=True)
    circuit_state = Column(String(20), nullable=False, default="CLOSED")
    consecutive_failures = Column(Integer, nullable=False, default=0)
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    failure_rate = Column(Float, nullable=False, default=0.0)


class WorkflowDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Workflow aggregate.
    """
    __tablename__ = "workflows"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    reference = Column(String(50), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False)
    active_version_number = Column(Integer, nullable=True)
    idempotency_key = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    archived_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_workflows_org_idempotency"),
        Index("ix_workflows_org_status", "organization_id", "status"),
    )


class WorkflowVersionDbModel(Base):
    """
    SQLAlchemy model representing an immutable definition version of a workflow.
    """
    __tablename__ = "workflow_versions"

    id = Column(String(50), primary_key=True)
    workflow_id = Column(String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    trigger = Column(Text, nullable=False)
    conditions = Column(Text, nullable=False)
    actions = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_wf_version"),
    )


class WorkflowExecutionDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Workflow Execution.
    """
    __tablename__ = "workflow_executions"

    id = Column(String(36), primary_key=True)
    workflow_id = Column(String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    source_event_id = Column(String(36), nullable=False, index=True)
    source_event_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True)
    action_results = Column(Text, nullable=False, default="[]") # JSON string list
    
    # Retry/backoff and dead-letter details
    retry_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_retry_at = Column(DateTime, nullable=True)
    failure_classification = Column(String(20), nullable=True)
    dead_lettered_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("workflow_id", "version", "source_event_id", name="uq_wf_executions_wf_ver_event"),
    )


class WorkflowCursorDbModel(Base):
    """
    SQLAlchemy model representing the last processed position in the transactional outbox.
    """
    __tablename__ = "workflow_cursors"

    consumer_name = Column(String(50), primary_key=True)
    last_event_created_at = Column(DateTime, nullable=False)


class WorkflowScheduledActionDbModel(Base):
    """
    SQLAlchemy model representing a scheduled action that should run after a delay.
    """
    __tablename__ = "workflow_scheduled_actions"

    id = Column(String(36), primary_key=True)
    execution_id = Column(String(36), ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    action_index = Column(Integer, nullable=False)
    action_data = Column(Text, nullable=False) # JSON string of action parameters
    scheduled_at = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="PENDING", index=True) # PENDING, COMPLETED, FAILED
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ReceivableCollectionActionDbModel(Base):
    """
    SQLAlchemy model representing a scheduled collection/dunning action.
    """
    __tablename__ = "receivable_collection_actions"

    id = Column(String(36), primary_key=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    receivable_id = Column(String(36), ForeignKey("receivables.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="PENDING", index=True) # PENDING, PROCESSING, COMPLETED, FAILED
    attempt_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    idempotency_key = Column(String(100), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    dead_lettered_at = Column(DateTime, nullable=True)
    failure_classification = Column(String(20), nullable=True)

    receivable = relationship("ReceivableDbModel")


class SettlementReconciliationDbModel(Base):
    """
    SQLAlchemy model representing the persistent state of a Settlement batch in the Reconciliation context.
    """
    __tablename__ = "settlement_reconciliations"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    reference = Column(String(50), nullable=False)
    merchant_reference = Column(String(50), nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    currency_code = Column(String(6), nullable=False)
    status = Column(String(20), nullable=False)
    reconciliation_state = Column(String(20), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    # Reconciliation result flat fields
    rec_verified_at = Column(DateTime, nullable=True)
    rec_description = Column(String(255), nullable=True)

    # Operational execution & retry state fields
    processing_state = Column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=3, server_default="3")
    last_error = Column(Text, nullable=True)
    failure_classification = Column(String(20), nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    dead_lettered_at = Column(DateTime, nullable=True)
    paused = Column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("organization_id", "reference", name="uq_settlements_rec_org_ref"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_settlements_rec_org_idempotency"),
    )

    entries = relationship("SettlementEntryDbModel", back_populates="settlement", cascade="all, delete-orphan")
    adjustments = relationship("SettlementAdjustmentDbModel", back_populates="settlement", cascade="all, delete-orphan")
    discrepancies = relationship("SettlementDiscrepancyDbModel", back_populates="settlement", cascade="all, delete-orphan")


class SettlementEntryDbModel(Base):
    """
    SQLAlchemy model representing a single entry line item inside a Settlement batch.
    """
    __tablename__ = "settlement_entries_reconciliation"

    id = Column(String(36), primary_key=True)
    settlement_id = Column(String(36), ForeignKey("settlement_reconciliations.id", ondelete="CASCADE"), nullable=False, index=True)
    source_transaction_reference = Column(String(100), nullable=False)
    source_context_type = Column(String(50), nullable=False)
    gross_amount = Column(String(50), nullable=False)
    fees_amount = Column(String(50), nullable=False)
    adjustments_amount = Column(String(50), nullable=False)
    net_amount = Column(String(50), nullable=False)
    currency_code = Column(String(6), nullable=False)
    reconciliation_state = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint("settlement_id", "source_transaction_reference", name="uq_settlement_entries_rec_source_tx"),
    )

    settlement = relationship("SettlementReconciliationDbModel", back_populates="entries")


class SettlementAdjustmentDbModel(Base):
    """
    SQLAlchemy model representing a financial adjustment applied to the Settlement batch.
    """
    __tablename__ = "settlement_adjustments_reconciliation"

    id = Column(String(50), primary_key=True)
    settlement_id = Column(String(36), ForeignKey("settlement_reconciliations.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(20), nullable=False)
    description = Column(String(255), nullable=False)
    amount = Column(String(50), nullable=False)
    currency_code = Column(String(6), nullable=False)

    settlement = relationship("SettlementReconciliationDbModel", back_populates="adjustments")


class SettlementDiscrepancyDbModel(Base):
    """
    SQLAlchemy model representing a discrepancy detected during reconciliation.
    """
    __tablename__ = "settlement_discrepancies"

    id = Column(String(36), primary_key=True)
    settlement_id = Column(String(36), ForeignKey("settlement_reconciliations.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_reference = Column(String(36), nullable=False)
    discrepancy_type = Column(String(50), nullable=False)
    expected_amount = Column(String(50), nullable=False)
    actual_amount = Column(String(50), nullable=False)
    difference_amount = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    resolved = Column(Boolean, nullable=False, default=False)

    settlement = relationship("SettlementReconciliationDbModel", back_populates="discrepancies")


class FinancialConsistencyViolationDbModel(Base):
    """
    SQLAlchemy model representing a cross-context financial invariant violation.
    """
    __tablename__ = "financial_consistency_violations"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    violation_type = Column(String(50), nullable=False)
    context = Column(String(50), nullable=False)
    entity_id = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, default="CRITICAL")
    classification = Column(String(50), nullable=False)
    expected_value = Column(String(255), nullable=True)
    actual_value = Column(String(255), nullable=True)
    description = Column(Text, nullable=False)
    resolved = Column(Boolean, nullable=False, default=False)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class FinancialRemediationAuditDbModel(Base):
    """
    SQLAlchemy model representing an audit log entry for a financial recovery action.
    """
    __tablename__ = "financial_remediation_audits"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)
    target_entity_id = Column(String(50), nullable=True, index=True)
    previous_state = Column(String(50), nullable=True)
    resulting_state = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False)
    error_message = Column(Text, nullable=True)
    correlation_id = Column(String(36), nullable=True)
    causation_id = Column(String(50), nullable=True)
    operator_id = Column(String(50), nullable=False)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TelemetryEventDbModel(Base):
    """
    SQLAlchemy model representing a structured operational telemetry event.
    """
    __tablename__ = "telemetry_events"

    id = Column(String(36), primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    component = Column(String(50), nullable=False, index=True)  # API, WORKER, WORKFLOW, DATABASE
    operation = Column(String(100), nullable=False, index=True)
    message = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False, default="INFO", index=True)  # INFO, WARNING, ERROR, CRITICAL
    entity_id = Column(String(50), nullable=True, index=True)
    previous_state = Column(String(50), nullable=True)
    resulting_state = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, index=True)  # SUCCESS, FAILED
    duration_ms = Column(Integer, nullable=True)
    correlation_id = Column(String(36), nullable=True, index=True)
    causation_id = Column(String(36), nullable=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    extra_metadata = Column(Text, nullable=True)  # JSON string

    __table_args__ = (
        Index("ix_telemetry_org_timestamp", "organization_id", "timestamp"),
        Index("ix_telemetry_org_comp_ts", "organization_id", "component", "timestamp"),
        Index("ix_telemetry_org_corr", "organization_id", "correlation_id"),
    )


class ComponentHealthTransitionDbModel(Base):
    """
    SQLAlchemy model representing a historical health state transition for a system component.
    """
    __tablename__ = "component_health_transitions"

    id = Column(String(36), primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    component = Column(String(50), nullable=False, index=True)  # api, ar_event_processor, settlement_automation_worker, database, platform
    previous_state = Column(String(20), nullable=False)  # HEALTHY, DEGRADED, UNHEALTHY, INACTIVE, UNKNOWN
    resulting_state = Column(String(20), nullable=False)
    evidence = Column(Text, nullable=False)  # JSON-serialized evidence string
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        Index("ix_health_trans_org_comp_ts", "organization_id", "component", "timestamp"),
    )


class AnomalyDbModel(Base):
    """
    SQLAlchemy model representing a detected system operational or business anomaly.
    """
    __tablename__ = "anomalies"

    id = Column(String(36), primary_key=True, index=True)
    anomaly_type = Column(String(50), nullable=False, index=True)  # WORKER_FAILURE_BURST, HIGH_LATENCY, REPEATED_VIOLATIONS
    severity = Column(String(20), nullable=False, index=True)  # WARNING, EXCEPTION, FINANCIAL_EXCEPTION, CRITICAL
    component = Column(String(50), nullable=False, index=True)  # api, ar_event_processor, database, etc.
    entity_id = Column(String(50), nullable=True, index=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    source = Column(String(50), nullable=False, default="RULE_ENGINE")
    observed_value = Column(String(255), nullable=True)
    expected_value = Column(String(255), nullable=True)
    evidence = Column(Text, nullable=False)  # JSON string of logs / context
    correlation_id = Column(String(36), nullable=True, index=True)
    causation_id = Column(String(36), nullable=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="DETECTED", index=True)  # DETECTED, ACKNOWLEDGED, ASSIGNED, INVESTIGATING, MITIGATED, RESOLVED, CLOSED
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    # Extended exception-management fields
    priority = Column(String(20), nullable=False, default="MEDIUM")  # LOW, MEDIUM, HIGH, CRITICAL
    category = Column(String(30), nullable=False, default="TECHNICAL")  # TECHNICAL, OPERATIONAL, FINANCIAL, SECURITY
    owner_id = Column(String(50), nullable=True)
    mitigation_notes = Column(Text, nullable=True)
    mitigated_at = Column(DateTime, nullable=True)
    root_cause = Column(Text, nullable=True)
    escalated = Column(Boolean, nullable=False, default=False)
    escalated_at = Column(DateTime, nullable=True)
    recurrence_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_anomalies_org_status", "organization_id", "status"),
        Index("ix_anomalies_org_detected", "organization_id", "detected_at"),
        Index("ix_anomalies_org_corr", "organization_id", "correlation_id"),
    )


class RemediationActionDbModel(Base):
    """
    SQLAlchemy model representing an automated remediation execution record.
    """
    __tablename__ = "remediation_actions"

    id = Column(String(36), primary_key=True, index=True)
    action_type = Column(String(50), nullable=False)  # RECOVER_WORKER, RETRY_RECONCILIATION
    target_component = Column(String(50), nullable=False)
    exception_id = Column(String(36), ForeignKey("anomalies.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_condition = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="PENDING", index=True)  # PENDING, EXECUTING, SUCCESS, FAILED, VERIFICATION_FAILED
    attempt_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    initiating_rule = Column(String(50), nullable=False)
    correlation_id = Column(String(36), nullable=True)
    causation_id = Column(String(36), nullable=True)
    execution_result = Column(Text, nullable=True)
    verification_result = Column(Text, nullable=True)
    error_info = Column(Text, nullable=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        Index("ix_remediation_org_status", "organization_id", "status"),
        Index("ix_remediation_org_exception", "organization_id", "exception_id"),
    )


class OperationalSituationDbModel(Base):
    """
    SQLAlchemy model representing an aggregated operational situation.
    """
    __tablename__ = "operational_situations"

    id = Column(String(36), primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)  # ACTIVE, RESOLVED
    impact_score = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    evidence = Column(Text, nullable=False)  # JSON metadata mapping causal chain & affected systems
    correlation_id = Column(String(36), nullable=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        Index("ix_situations_org_status", "organization_id", "status"),
        Index("ix_situations_org_corr", "organization_id", "correlation_id"),
    )


class OperationalAuditTrailDbModel(Base):
    """
    SQLAlchemy model representing an operational audit log of control-plane operator interventions.
    """
    __tablename__ = "operational_audit_trails"

    id = Column(String(36), primary_key=True, index=True)
    actor_id = Column(String(50), nullable=False)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(50), nullable=False)  # ASSIGN_EXCEPTION, MITIGATE_EXCEPTION, ESCALATE_EXCEPTION, RETRY_REMEDIATION, etc.
    target_id = Column(String(36), nullable=False, index=True)
    target_type = Column(String(30), nullable=False)  # exception, remediation, situation
    previous_state = Column(Text, nullable=True)
    new_state = Column(Text, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    context = Column(Text, nullable=True)
    correlation_id = Column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_audit_org_ts", "organization_id", "timestamp"),
    )


class AutonomousReadinessPolicyDbModel(Base):
    """
    SQLAlchemy model representing a tenant's autonomous readiness policy configuration.
    """
    __tablename__ = "autonomous_readiness_policies"

    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True, index=True)
    readiness_level = Column(String(30), nullable=False, default="LEVEL_2_CONTROLLED_AUTOMATION")  # LEVEL_0_OBSERVATIONAL, LEVEL_1_ASSISTED, LEVEL_2_CONTROLLED_AUTOMATION
    emergency_disable = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class OperatorApprovalRequestDbModel(Base):
    """
    SQLAlchemy model representing a human-in-the-loop approval request for autonomous actions.
    """
    __tablename__ = "operator_approval_requests"

    id = Column(String(36), primary_key=True, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    request_type = Column(String(50), nullable=False)  # e.g. REMEDIATION_EXECUTION, SETTLEMENT_REVERSAL
    target_id = Column(String(50), nullable=False)  # ID of target exception or settlement
    status = Column(String(20), nullable=False, default="PENDING", index=True)  # PENDING, APPROVED, REJECTED
    description = Column(Text, nullable=False)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String(50), nullable=True)  # User ID of the decider
    decision_notes = Column(Text, nullable=True)







