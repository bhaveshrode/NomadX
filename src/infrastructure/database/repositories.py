import json
import contextvars
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import uuid4
from datetime import datetime

# Context variables for event correlation/causation tracking
current_correlation_id = contextvars.ContextVar("current_correlation_id", default=None)
current_causation_id = contextvars.ContextVar("current_causation_id", default=None)
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...domain.organization.organization import Organization, OrganizationStatus, Membership, OrganizationRole
from ...domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    OrganizationSettings,
    UserId,
)
from ...domain.organization.repositories import IOrganizationRepository

from ...domain.identity.user import User, UserStatus
from ...domain.identity.value_objects import Email, Password
from ...domain.identity.repositories import IUserRepository

from ...domain.notification.repositories import INotificationRepository
from ...domain.notification.notification import Notification, NotificationStatus
from ...domain.notification.value_objects import (
    NotificationId,
    NotificationReference,
    NotificationRecipient,
    NotificationChannel,
    NotificationType,
    NotificationPriority,
    NotificationTemplateId,
    NotificationPayload,
    NotificationFailure,
    NotificationFailureCode,
    NotificationIdempotencyKey
)
from ...domain.notification.template import (
    NotificationTemplate,
    TemplateId,
    TemplateReference,
    TemplateVersion,
    TemplateContent,
    TemplateStatus
)
from ...domain.notification.repositories import INotificationTemplateRepository, INotificationPreferenceRepository
from ...domain.notification.preferences import NotificationPreference, PreferenceId
from .models import NotificationTemplateDbModel, NotificationPreferenceDbModel

from ...domain.shared.specifications.specification import Specification
from sqlalchemy.exc import IntegrityError
from .models import (
    OrganizationDbModel,
    UserDbModel,
    MembershipDbModel,
    OutboxEventDbModel,
    InvoiceDbModel,
    InvoiceLineItemDbModel,
    InvoiceTaxLineDbModel,
    InvoiceAdjustmentDbModel,
    PaymentIntentDbModel,
    PaymentDbModel,
    PaymentAllocationDbModel,
    ReceivableDbModel,
    SettlementDbModel,
    NotificationDbModel,
    WorkflowDbModel,
    WorkflowVersionDbModel
)

from ...domain.invoice import (
    Invoice,
    InvoiceStatus,
    InvoiceLineItem,
    LineItemId,
    InvoiceId,
    CustomerId,
    InvoiceNumber,
    Currency,
    CurrencyType,
    Money,
    BillingSnapshot,
    InvoiceOrigin,
    InvoiceOriginType,
    TaxLine,
    Adjustment,
    AdjustmentType,
    PaymentTerms,
    PaymentTermsType,
    InvoiceTotals
)
from ...domain.invoice.repositories import IInvoiceRepository

from ...domain.payment import (
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentId,
    PaymentId,
    PaymentReference,
    PaymentAsset,
    PaymentNetwork,
    PaymentDestination,
    PaymentMethod,
    Payment,
    PaymentStatus
)
from ...domain.payment.payment import ReconciliationStatus
from ...domain.payment.repositories import IPaymentIntentRepository, IPaymentRepository
from ...domain.receivable.repositories import IReceivableRepository
from ...domain.settlement.repositories import ISettlementRepository
from ...domain.settlement.settlement import Settlement, SettlementStatus
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

class PostgresOrganizationRepository(IOrganizationRepository):
    """
    SQLAlchemy-backed implementation of IOrganizationRepository.
    Integrates transactional outbox patterns to persist domain events atomically.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_domain(self, model: OrganizationDbModel, mem_models: List[MembershipDbModel]) -> Organization:
        """Maps persistent models to domain aggregate root including membership entities."""
        org_id = OrganizationId(model.id)
        name = OrganizationName(model.name)
        slug = OrganizationSlug(model.slug)
        owner_id = UserId(model.owner_id)
        settings = OrganizationSettings(model.default_currency)
        
        memberships: Dict[str, Membership] = {}
        for m in mem_models:
            memberships[m.user_id] = Membership(
                user_id=UserId(m.user_id),
                role=OrganizationRole(m.role),
                joined_at=m.joined_at,
                status=m.status
            )

        props = {
            "name": name,
            "slug": slug,
            "status": OrganizationStatus(model.status),
            "settings": settings,
            "owner_id": owner_id,
            "memberships": memberships,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return Organization(org_id, props)

    def _to_db(self, domain: Organization) -> OrganizationDbModel:
        """Maps domain aggregate root to persistent model."""
        return OrganizationDbModel(
            id=domain.id.value,
            name=domain.name.value,
            slug=domain.slug.value,
            status=domain.status.value,
            default_currency=domain.settings.default_currency,
            owner_id=domain.owner_id.value,
            created_at=domain.created_at,
            updated_at=domain.updated_at
        )

    async def find_by_id(self, id: OrganizationId) -> Optional[Organization]:
        result = await self._session.execute(
            select(OrganizationDbModel).where(OrganizationDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        mem_result = await self._session.execute(
            select(MembershipDbModel).where(MembershipDbModel.organization_id == id.value)
        )
        mem_models = mem_result.scalars().all()
        return self._to_domain(model, mem_models)

    async def exists(self, id: OrganizationId) -> bool:
        result = await self._session.execute(
            select(OrganizationDbModel.id).where(OrganizationDbModel.id == id.value)
        )
        return result.scalar_one_or_none() is not None

    async def find_all(self) -> List[Organization]:
        result = await self._session.execute(select(OrganizationDbModel))
        models = result.scalars().all()
        
        orgs = []
        for m in models:
            mem_result = await self._session.execute(
                select(MembershipDbModel).where(MembershipDbModel.organization_id == m.id)
            )
            mem_models = mem_result.scalars().all()
            orgs.append(self._to_domain(m, mem_models))
        return orgs

    async def find_by_slug(self, slug: OrganizationSlug) -> Optional[Organization]:
        result = await self._session.execute(
            select(OrganizationDbModel).where(OrganizationDbModel.slug == slug.value)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        mem_result = await self._session.execute(
            select(MembershipDbModel).where(MembershipDbModel.organization_id == model.id)
        )
        mem_models = mem_result.scalars().all()
        return self._to_domain(model, mem_models)

    async def find_by_specification(
        self, specification: Specification[Organization]
    ) -> List[Organization]:
        all_orgs = await self.find_all()
        return [org for org in all_orgs if specification.is_satisfied_by(org)]

    async def save(self, aggregate: Organization) -> None:
        result = await self._session.execute(
            select(OrganizationDbModel).where(OrganizationDbModel.id == aggregate.id.value)
        )
        existing_model = result.scalar_one_or_none()

        if existing_model:
            existing_model.name = aggregate.name.value
            existing_model.slug = aggregate.slug.value
            existing_model.status = aggregate.status.value
            existing_model.default_currency = aggregate.settings.default_currency
            existing_model.owner_id = aggregate.owner_id.value
            existing_model.updated_at = aggregate.updated_at
        else:
            model = self._to_db(aggregate)
            self._session.add(model)

        await self._session.flush()

        # Upsert Membership sub-entities
        for m in aggregate.memberships.values():
            m_result = await self._session.execute(
                select(MembershipDbModel)
                .where(MembershipDbModel.organization_id == aggregate.id.value)
                .where(MembershipDbModel.user_id == m.user_id.value)
            )
            existing_m = m_result.scalar_one_or_none()
            if existing_m:
                existing_m.role = m.role.value
                existing_m.status = m.status
            else:
                db_m = MembershipDbModel(
                    organization_id=aggregate.id.value,
                    user_id=m.user_id.value,
                    role=m.role.value,
                    joined_at=m.joined_at,
                    status=m.status
                )
                self._session.add(db_m)

        # Delete memberships from DB that were removed from aggregate
        db_mems_result = await self._session.execute(
            select(MembershipDbModel.user_id).where(MembershipDbModel.organization_id == aggregate.id.value)
        )
        db_user_ids = db_mems_result.scalars().all()
        for uid in db_user_ids:
            if uid not in aggregate.memberships:
                await self._session.execute(
                    delete(MembershipDbModel)
                    .where(MembershipDbModel.organization_id == aggregate.id.value)
                    .where(MembershipDbModel.user_id == uid)
                )

        # Transactional Outbox
        for event in aggregate.domain_events:
            event_id = str(uuid4())
            if event.event_name == "OrganizationCreated":
                payload_dict = {
                    "organization_id": event.organization_id.value,
                    "owner_id": event.owner_id.value,
                    "occurred_on": event.occurred_on.isoformat()
                }
            elif event.event_name == "MembershipCreated":
                payload_dict = {
                    "organization_id": event.organization_id.value,
                    "user_id": event.user_id.value,
                    "role": event.role,
                    "occurred_on": event.occurred_on.isoformat()
                }
            else:
                payload_dict = {}

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        aggregate.clear_domain_events()

    async def delete(self, id: OrganizationId) -> None:
        result = await self._session.execute(
            select(OrganizationDbModel).where(OrganizationDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        if model:
            await self._session.delete(model)


class PostgresUserRepository(IUserRepository):
    """
    SQLAlchemy-backed implementation of IUserRepository.
    Supports transactional outbox patterns to persist domain events atomically.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_domain(self, model: UserDbModel) -> User:
        """Maps persistent model to domain aggregate root."""
        user_id = UserId(model.id)
        email = Email(model.email)
        password = Password.from_hash(model.password_hash)
        
        props = {
            "email": email,
            "password": password,
            "name": model.name,
            "status": UserStatus(model.status),
            "email_verified": model.email_verified,
            "verification_token": model.verification_token,
            "login_attempts": model.login_attempts,
            "locked_until": model.locked_until,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return User(user_id, props)

    def _to_db(self, domain: User) -> UserDbModel:
        """Maps domain aggregate root to persistent model."""
        return UserDbModel(
            id=domain.id.value,
            email=domain.email.value,
            password_hash=domain.password.password_hash,
            name=domain.name,
            status=domain.status.value,
            email_verified=domain.email_verified,
            verification_token=domain.verification_token,
            login_attempts=domain.login_attempts,
            locked_until=domain.locked_until,
            created_at=domain.created_at,
            updated_at=domain.updated_at
        )

    async def find_by_id(self, id: UserId) -> Optional[User]:
        result = await self._session.execute(
            select(UserDbModel).where(UserDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def exists(self, id: UserId) -> bool:
        result = await self._session.execute(
            select(UserDbModel.id).where(UserDbModel.id == id.value)
        )
        return result.scalar_one_or_none() is not None

    async def find_all(self) -> List[User]:
        result = await self._session.execute(select(UserDbModel))
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

    async def find_by_email(self, email: Email) -> Optional[User]:
        result = await self._session.execute(
            select(UserDbModel).where(UserDbModel.email == email.value)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def find_by_specification(
        self, specification: Specification[User]
    ) -> List[User]:
        all_users = await self.find_all()
        return [u for u in all_users if specification.is_satisfied_by(u)]

    async def save(self, aggregate: User) -> None:
        result = await self._session.execute(
            select(UserDbModel).where(UserDbModel.id == aggregate.id.value)
        )
        existing_model = result.scalar_one_or_none()

        if existing_model:
            existing_model.email = aggregate.email.value
            existing_model.password_hash = aggregate.password.password_hash
            existing_model.name = aggregate.name
            existing_model.status = aggregate.status.value
            existing_model.email_verified = aggregate.email_verified
            existing_model.verification_token = aggregate.verification_token
            existing_model.login_attempts = aggregate.login_attempts
            existing_model.locked_until = aggregate.locked_until
            existing_model.updated_at = aggregate.updated_at
        else:
            model = self._to_db(aggregate)
            self._session.add(model)

        # Transactional Outbox
        for event in aggregate.domain_events:
            event_id = str(uuid4())
            if event.event_name == "UserRegistered":
                payload_dict = {
                    "user_id": event.user_id.value,
                    "email": event.email.value,
                    "name": event.name,
                    "occurred_on": event.occurred_on.isoformat()
                }
            elif event.event_name == "EmailVerified":
                payload_dict = {
                    "user_id": event.user_id.value,
                    "occurred_on": event.occurred_on.isoformat()
                }
            elif event.event_name == "UserAuthenticated":
                payload_dict = {
                    "user_id": event.user_id.value,
                    "occurred_on": event.occurred_on.isoformat()
                }
            elif event.event_name == "UserLockedOut":
                payload_dict = {
                    "user_id": event.user_id.value,
                    "locked_until": event.locked_until.isoformat(),
                    "occurred_on": event.occurred_on.isoformat()
                }
            else:
                payload_dict = {}

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        aggregate.clear_domain_events()

    async def delete(self, id: UserId) -> None:
        result = await self._session.execute(
            select(UserDbModel).where(UserDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        if model:
            await self._session.delete(model)


class PostgresInvoiceRepository(IInvoiceRepository):
    """
    SQLAlchemy-backed implementation of IInvoiceRepository.
    Integrates transactional outbox patterns to persist domain events atomically.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_domain(
        self,
        model: InvoiceDbModel,
        line_item_models: List[InvoiceLineItemDbModel],
        tax_line_models: List[InvoiceTaxLineDbModel],
        adjustment_models: List[InvoiceAdjustmentDbModel]
    ) -> Invoice:
        """Maps persistent relational models back to domain Invoice aggregate root."""
        invoice_id = InvoiceId(model.id)
        org_id = OrganizationId(model.organization_id)
        invoice_number = InvoiceNumber(model.invoice_number) if model.invoice_number else None
        
        origin = InvoiceOrigin(
            type=InvoiceOriginType(model.origin_type),
            source_system=model.source_system,
            external_invoice_id=model.external_invoice_id
        )
        
        cust_id = CustomerId(model.customer_id) if model.customer_id else None
        
        billing_snapshot = BillingSnapshot(
            legal_name=model.billing_name,
            email=model.billing_email,
            address=model.billing_address,
            country=model.billing_country,
            tax_identifier=model.billing_tax_identifier
        ) if model.billing_name else None

        currency = Currency(
            code=model.currency_code,
            type=CurrencyType(model.currency_type)
        )

        status = InvoiceStatus(model.status)

        payment_terms = PaymentTerms(
            terms_type=PaymentTermsType(model.payment_terms_type),
            due_at=model.due_at
        )

        # Reconstruct line items
        line_items = []
        for m in line_item_models:
            line_items.append(
                InvoiceLineItem(
                    id=LineItemId(m.id),
                    description=m.description,
                    quantity=Decimal(m.quantity),
                    unit_price=Money(Decimal(m.unit_price), currency),
                    discount=Money(Decimal(m.discount), currency),
                    tax=Money(Decimal(m.tax), currency)
                )
            )

        # Reconstruct tax lines
        tax_lines = []
        for m in tax_line_models:
            tax_lines.append(
                TaxLine(
                    code=m.code,
                    name=m.name,
                    rate=Decimal(m.rate),
                    taxable_amount=Money(Decimal(m.taxable_amount), currency),
                    amount=Money(Decimal(m.amount), currency)
                )
            )

        # Reconstruct adjustments
        adjustments = []
        for m in adjustment_models:
            adjustments.append(
                Adjustment(
                    type=AdjustmentType(m.type),
                    description=m.description,
                    amount=Money(Decimal(m.amount), currency),
                    rate=Decimal(m.rate) if m.rate else None
                )
            )

        # Reconstruct totals
        totals = InvoiceTotals(
            subtotal=Money(Decimal(model.subtotal), currency),
            discount_total=Money(Decimal(model.discount_total), currency),
            charge_total=Money(Decimal(model.charge_total), currency),
            tax_total=Money(Decimal(model.tax_total), currency),
            grand_total=Money(Decimal(model.grand_total), currency)
        )

        props = {
            "organization_id": org_id,
            "invoice_number": invoice_number,
            "origin": origin,
            "customer_id": cust_id,
            "billing_snapshot": billing_snapshot,
            "currency": currency,
            "line_items": line_items,
            "adjustments": adjustments,
            "taxes": tax_lines,
            "totals": totals,
            "payment_terms": payment_terms,
            "lifecycle_status": status,
            "issued_at": model.issued_at,
            "due_at": model.due_at,
            "cancelled_at": model.cancelled_at,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        
        invoice = Invoice(invoice_id, props)
        return invoice

    def _to_db(self, domain: Invoice) -> InvoiceDbModel:
        """Maps domain aggregate root to persistent database model."""
        return InvoiceDbModel(
            id=domain.id.value,
            organization_id=domain.organization_id.value,
            invoice_number=domain.invoice_number.value if domain.invoice_number else None,
            origin_type=domain.origin.type.value,
            source_system=domain.origin.source_system,
            external_invoice_id=domain.origin.external_invoice_id,
            customer_id=domain.customer_id.value if domain.customer_id else None,
            billing_name=domain.billing_snapshot.legal_name if domain.billing_snapshot else None,
            billing_email=domain.billing_snapshot.email if domain.billing_snapshot else None,
            billing_address=domain.billing_snapshot.address if domain.billing_snapshot else None,
            billing_country=domain.billing_snapshot.country if domain.billing_snapshot else None,
            billing_tax_identifier=domain.billing_snapshot.tax_identifier if domain.billing_snapshot else None,
            currency_code=domain.currency.code,
            currency_type=domain.currency.type.value,
            status=domain.lifecycle_status.value,
            subtotal=str(domain.totals.subtotal.amount),
            discount_total=str(domain.totals.discount_total.amount),
            charge_total=str(domain.totals.charge_total.amount),
            tax_total=str(domain.totals.tax_total.amount),
            grand_total=str(domain.totals.grand_total.amount),
            payment_terms_type=domain.payment_terms.terms_type.value,
            due_at=domain.due_at,
            issued_at=domain.issued_at,
            cancelled_at=domain.cancelled_at,
            created_at=domain.created_at,
            updated_at=domain.updated_at
        )

    async def find_by_id(self, id: InvoiceId) -> Optional[Invoice]:
        result = await self._session.execute(
            select(InvoiceDbModel).where(InvoiceDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        # Line items ordered by position
        line_items_res = await self._session.execute(
            select(InvoiceLineItemDbModel)
            .where(InvoiceLineItemDbModel.invoice_id == id.value)
            .order_by(InvoiceLineItemDbModel.position)
        )
        line_item_models = line_items_res.scalars().all()

        # Tax lines
        tax_lines_res = await self._session.execute(
            select(InvoiceTaxLineDbModel)
            .where(InvoiceTaxLineDbModel.invoice_id == id.value)
        )
        tax_line_models = tax_lines_res.scalars().all()

        # Adjustments
        adjustments_res = await self._session.execute(
            select(InvoiceAdjustmentDbModel)
            .where(InvoiceAdjustmentDbModel.invoice_id == id.value)
        )
        adjustment_models = adjustments_res.scalars().all()

        return self._to_domain(model, line_item_models, tax_line_models, adjustment_models)

    async def exists(self, id: InvoiceId) -> bool:
        result = await self._session.execute(
            select(InvoiceDbModel.id).where(InvoiceDbModel.id == id.value)
        )
        return result.scalar_one_or_none() is not None

    async def find_by_invoice_number(
        self,
        organization_id: OrganizationId,
        invoice_number: InvoiceNumber
    ) -> Optional[Invoice]:
        result = await self._session.execute(
            select(InvoiceDbModel)
            .where(InvoiceDbModel.organization_id == organization_id.value)
            .where(InvoiceDbModel.invoice_number == invoice_number.value)
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        # Line items
        line_items_res = await self._session.execute(
            select(InvoiceLineItemDbModel)
            .where(InvoiceLineItemDbModel.invoice_id == model.id)
            .order_by(InvoiceLineItemDbModel.position)
        )
        line_item_models = line_items_res.scalars().all()

        # Tax lines
        tax_lines_res = await self._session.execute(
            select(InvoiceTaxLineDbModel)
            .where(InvoiceTaxLineDbModel.invoice_id == model.id)
        )
        tax_line_models = tax_lines_res.scalars().all()

        # Adjustments
        adjustments_res = await self._session.execute(
            select(InvoiceAdjustmentDbModel)
            .where(InvoiceAdjustmentDbModel.invoice_id == model.id)
        )
        adjustment_models = adjustments_res.scalars().all()

        return self._to_domain(model, line_item_models, tax_line_models, adjustment_models)

    async def find_by_organization(
        self,
        organization_id: OrganizationId
    ) -> List[Invoice]:
        result = await self._session.execute(
            select(InvoiceDbModel).where(InvoiceDbModel.organization_id == organization_id.value)
        )
        models = result.scalars().all()

        invoices = []
        for model in models:
            # Line items
            line_items_res = await self._session.execute(
                select(InvoiceLineItemDbModel)
                .where(InvoiceLineItemDbModel.invoice_id == model.id)
                .order_by(InvoiceLineItemDbModel.position)
            )
            line_item_models = line_items_res.scalars().all()

            # Tax lines
            tax_lines_res = await self._session.execute(
                select(InvoiceTaxLineDbModel)
                .where(InvoiceTaxLineDbModel.invoice_id == model.id)
            )
            tax_line_models = tax_lines_res.scalars().all()

            # Adjustments
            adjustments_res = await self._session.execute(
                select(InvoiceAdjustmentDbModel)
                .where(InvoiceAdjustmentDbModel.invoice_id == model.id)
            )
            adjustment_models = adjustments_res.scalars().all()

            invoices.append(self._to_domain(model, line_item_models, tax_line_models, adjustment_models))

        return invoices

    async def find_all(self) -> List[Invoice]:
        result = await self._session.execute(select(InvoiceDbModel))
        models = result.scalars().all()

        invoices = []
        for model in models:
            # Line items
            line_items_res = await self._session.execute(
                select(InvoiceLineItemDbModel)
                .where(InvoiceLineItemDbModel.invoice_id == model.id)
                .order_by(InvoiceLineItemDbModel.position)
            )
            line_item_models = line_items_res.scalars().all()

            # Tax lines
            tax_lines_res = await self._session.execute(
                select(InvoiceTaxLineDbModel)
                .where(InvoiceTaxLineDbModel.invoice_id == model.id)
            )
            tax_line_models = tax_lines_res.scalars().all()

            # Adjustments
            adjustments_res = await self._session.execute(
                select(InvoiceAdjustmentDbModel)
                .where(InvoiceAdjustmentDbModel.invoice_id == model.id)
            )
            adjustment_models = adjustments_res.scalars().all()

            invoices.append(self._to_domain(model, line_item_models, tax_line_models, adjustment_models))

        return invoices

    async def find_by_specification(
        self, specification: Specification[Invoice]
    ) -> List[Invoice]:
        all_invoices = await self.find_all()
        return [inv for inv in all_invoices if specification.is_satisfied_by(inv)]

    async def save(self, aggregate: Invoice) -> None:
        result = await self._session.execute(
            select(InvoiceDbModel).where(InvoiceDbModel.id == aggregate.id.value)
        )
        existing_model = result.scalar_one_or_none()

        if existing_model:
            existing_model.invoice_number = aggregate.invoice_number.value if aggregate.invoice_number else None
            existing_model.origin_type = aggregate.origin.type.value
            existing_model.source_system = aggregate.origin.source_system
            existing_model.external_invoice_id = aggregate.origin.external_invoice_id
            existing_model.customer_id = aggregate.customer_id.value if aggregate.customer_id else None
            existing_model.billing_name = aggregate.billing_snapshot.legal_name if aggregate.billing_snapshot else None
            existing_model.billing_email = aggregate.billing_snapshot.email if aggregate.billing_snapshot else None
            existing_model.billing_address = aggregate.billing_snapshot.address if aggregate.billing_snapshot else None
            existing_model.billing_country = aggregate.billing_snapshot.country if aggregate.billing_snapshot else None
            existing_model.billing_tax_identifier = aggregate.billing_snapshot.tax_identifier if aggregate.billing_snapshot else None
            existing_model.status = aggregate.lifecycle_status.value
            existing_model.subtotal = str(aggregate.totals.subtotal.amount)
            existing_model.discount_total = str(aggregate.totals.discount_total.amount)
            existing_model.charge_total = str(aggregate.totals.charge_total.amount)
            existing_model.tax_total = str(aggregate.totals.tax_total.amount)
            existing_model.grand_total = str(aggregate.totals.grand_total.amount)
            existing_model.payment_terms_type = aggregate.payment_terms.terms_type.value
            existing_model.due_at = aggregate.due_at
            existing_model.issued_at = aggregate.issued_at
            existing_model.cancelled_at = aggregate.cancelled_at
            existing_model.updated_at = aggregate.updated_at
        else:
            model = self._to_db(aggregate)
            self._session.add(model)

        # Clear and sync line items
        await self._session.execute(
            delete(InvoiceLineItemDbModel).where(InvoiceLineItemDbModel.invoice_id == aggregate.id.value)
        )
        for idx, item in enumerate(aggregate.line_items):
            db_item = InvoiceLineItemDbModel(
                id=item.id.value,
                invoice_id=aggregate.id.value,
                description=item.description,
                quantity=str(item.quantity),
                unit_price=str(item.unit_price.amount),
                discount=str(item.discount.amount),
                tax=str(item.tax.amount),
                currency=item.unit_price.currency.code,
                amount=str(item.amount.amount),
                position=idx
            )
            self._session.add(db_item)

        # Clear and sync adjustments
        await self._session.execute(
            delete(InvoiceAdjustmentDbModel).where(InvoiceAdjustmentDbModel.invoice_id == aggregate.id.value)
        )
        for adj in aggregate.adjustments:
            db_adj = InvoiceAdjustmentDbModel(
                id=str(uuid4()),
                invoice_id=aggregate.id.value,
                type=adj.type.value,
                description=adj.description,
                rate=str(adj.rate) if adj.rate is not None else None,
                amount=str(adj.amount.amount)
            )
            self._session.add(db_adj)

        # Clear and sync tax lines
        await self._session.execute(
            delete(InvoiceTaxLineDbModel).where(InvoiceTaxLineDbModel.invoice_id == aggregate.id.value)
        )
        for tax in aggregate.taxes:
            db_tax = InvoiceTaxLineDbModel(
                id=str(uuid4()),
                invoice_id=aggregate.id.value,
                code=tax.code,
                name=tax.name,
                rate=str(tax.rate),
                taxable_amount=str(tax.taxable_amount.amount),
                amount=str(tax.amount.amount)
            )
            self._session.add(db_tax)

        # Transactional Outbox for Invoice events
        for event in aggregate.domain_events:
            event_id = str(uuid4())
            if event.event_name == "InvoiceCreated":
                payload_dict = {
                    "invoice_id": event.invoice_id.value,
                    "organization_id": event.organization_id.value,
                    "occurred_on": event.occurred_on.isoformat()
                }
            elif event.event_name == "InvoiceIssued":
                payload_dict = {
                    "invoice_id": event.invoice_id.value,
                    "invoice_number": event.invoice_number.value,
                    "organization_id": event.organization_id.value,
                    "occurred_on": event.occurred_on.isoformat()
                }
            elif event.event_name == "InvoiceVoided":
                payload_dict = {
                    "invoice_id": event.invoice_id.value,
                    "organization_id": event.organization_id.value,
                    "occurred_on": event.occurred_on.isoformat()
                }
            else:
                payload_dict = {}

            corr_id = current_correlation_id.get()
            caus_id = current_causation_id.get()
            if corr_id or caus_id:
                payload_dict["_metadata"] = {
                    "correlation_id": corr_id,
                    "causation_id": caus_id
                }

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        aggregate.clear_domain_events()

    async def delete(self, id: InvoiceId) -> None:
        result = await self._session.execute(
            select(InvoiceDbModel).where(InvoiceDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        if model:
            # Enforce delete policy (raise error if status is not DRAFT)
            if model.status != InvoiceStatus.DRAFT.value:
                raise ValueError(
                    f"Audit Trail Protection: Cannot delete an invoice in {model.status} state. "
                    f"Only DRAFT invoices can be deleted."
                )
            await self._session.delete(model)


class PostgresPaymentIntentRepository(IPaymentIntentRepository):
    """
    SQLAlchemy-backed implementation of IPaymentIntentRepository.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: PaymentIntentDbModel) -> PaymentIntent:
        currency_type = CurrencyType.DIGITAL_ASSET if model.asset_type == "DIGITAL_ASSET" else CurrencyType.FIAT
        currency = Currency(model.asset_code, currency_type)
        money = Money(Decimal(model.requested_amount), currency)
        
        asset = PaymentAsset(model.asset_code, model.asset_type, model.asset_decimals)
        
        destination = PaymentDestination(
            type=model.destination_type,
            identifier=model.destination_identifier,
            network=model.network
        )
        
        props = {
            "organization_id": OrganizationId(model.organization_id),
            "invoice_id": InvoiceId(model.invoice_id),
            "reference": PaymentReference(model.payment_reference),
            "requested_amount": money,
            "requested_asset": asset,
            "destination": destination,
            "payment_method": PaymentMethod(model.payment_method),
            "status": PaymentIntentStatus(model.status),
            "expires_at": model.expires_at,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return PaymentIntent(PaymentIntentId(model.id), props)

    async def save(self, payment_intent: PaymentIntent) -> None:
        db_model = PaymentIntentDbModel(
            id=payment_intent.id.value,
            organization_id=payment_intent.organization_id.value,
            invoice_id=payment_intent.invoice_id.value,
            payment_reference=payment_intent.reference.value,
            requested_amount=str(payment_intent.requested_amount.amount),
            asset_code=payment_intent.requested_asset.code,
            asset_type=payment_intent.requested_asset.type,
            asset_decimals=payment_intent.requested_asset.decimals,
            network=payment_intent.destination.network,
            destination_type=payment_intent.destination.type,
            destination_identifier=payment_intent.destination.identifier,
            payment_method=payment_intent.payment_method.value,
            status=payment_intent.status.value,
            expires_at=payment_intent.expires_at,
            created_at=payment_intent.created_at,
            updated_at=payment_intent.updated_at
        )
        
        await self._session.merge(db_model)
        
        # Transactional Outbox
        for event in payment_intent.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "payment_intent_id": event.payment_intent_id.value,
                "organization_id": event.organization_id.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)
            
        payment_intent.clear_domain_events()

    async def find_by_id(self, id: PaymentIntentId) -> Optional[PaymentIntent]:
        result = await self._session.execute(
            select(PaymentIntentDbModel).where(PaymentIntentDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_reference(self, reference: PaymentReference) -> Optional[PaymentIntent]:
        result = await self._session.execute(
            select(PaymentIntentDbModel).where(PaymentIntentDbModel.payment_reference == reference.value)
        )
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_invoice(self, invoice_id: InvoiceId) -> List[PaymentIntent]:
        result = await self._session.execute(
            select(PaymentIntentDbModel).where(PaymentIntentDbModel.invoice_id == invoice_id.value)
        )
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_by_organization(self, organization_id: OrganizationId) -> List[PaymentIntent]:
        result = await self._session.execute(
            select(PaymentIntentDbModel).where(PaymentIntentDbModel.organization_id == organization_id.value)
        )
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]


class PostgresPaymentRepository(IPaymentRepository):
    """
    SQLAlchemy-backed implementation of IPaymentRepository.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: PaymentDbModel) -> Payment:
        currency_type = CurrencyType.DIGITAL_ASSET if model.asset_type == "DIGITAL_ASSET" else CurrencyType.FIAT
        currency = Currency(model.asset_code, currency_type)
        money = Money(Decimal(model.observed_amount), currency)
        
        asset = PaymentAsset(model.asset_code, model.asset_type, model.asset_decimals)
        network = PaymentNetwork(model.network, "BLOCKCHAIN" if currency_type == CurrencyType.DIGITAL_ASSET else "BANK")
        
        intent_id = PaymentIntentId(model.payment_intent_id) if model.payment_intent_id else None
        
        props = {
            "organization_id": OrganizationId(model.organization_id),
            "payment_intent_id": intent_id,
            "observed_amount": money,
            "asset": asset,
            "network": network,
            "transaction_reference": model.transaction_reference,
            "sender": model.sender,
            "receiver": model.receiver,
            "status": PaymentStatus(model.status),
            "reconciliation_status": ReconciliationStatus(model.reconciliation_status) if getattr(model, "reconciliation_status", None) else ReconciliationStatus.UNRECONCILED,
            "observed_at": model.observed_at,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return Payment(PaymentId(model.id), props)

    async def save(self, payment: Payment) -> None:
        db_model = PaymentDbModel(
            id=payment.id.value,
            organization_id=payment.organization_id.value,
            payment_intent_id=payment.payment_intent_id.value if payment.payment_intent_id else None,
            observed_amount=str(payment.observed_amount.amount),
            asset_code=payment.asset.code,
            asset_type=payment.asset.type,
            asset_decimals=payment.asset.decimals,
            network=payment.network.code,
            transaction_reference=payment.transaction_reference,
            sender=payment.sender,
            receiver=payment.receiver,
            status=payment.status.value,
            reconciliation_status=payment.reconciliation_status.value,
            observed_at=payment.observed_at,
            created_at=payment.created_at,
            updated_at=payment.updated_at
        )
        
        try:
            await self._session.merge(db_model)
            await self._session.flush()
        except IntegrityError:
            raise ValueError("PAYMENT_ALREADY_EXISTS")
            
        # Transactional Outbox
        for event in payment.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "payment_id": event.payment_id.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)
            
        payment.clear_domain_events()

    async def find_by_id(self, id: PaymentId) -> Optional[Payment]:
        result = await self._session.execute(
            select(PaymentDbModel).where(PaymentDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_transaction_reference(
        self,
        network: PaymentNetwork,
        transaction_reference: str
    ) -> Optional[Payment]:
        result = await self._session.execute(
            select(PaymentDbModel).where(
                PaymentDbModel.network == network.code,
                PaymentDbModel.transaction_reference == transaction_reference
            )
        )
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_intent(self, intent_id: PaymentIntentId) -> List[Payment]:
        result = await self._session.execute(
            select(PaymentDbModel).where(PaymentDbModel.payment_intent_id == intent_id.value)
        )
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_by_organization(self, organization_id: OrganizationId) -> List[Payment]:
        result = await self._session.execute(
            select(PaymentDbModel).where(PaymentDbModel.organization_id == organization_id.value)
        )
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]


from ...domain.payment.repositories import IPaymentAllocationRepository
from ...domain.payment.allocation import PaymentAllocation
from ...domain.payment.value_objects import PaymentAllocationId

class PostgresPaymentAllocationRepository(IPaymentAllocationRepository):
    """
    SQLAlchemy-backed implementation of IPaymentAllocationRepository.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: PaymentAllocationDbModel) -> PaymentAllocation:
        currency = Currency(model.currency, CurrencyType.DIGITAL_ASSET)
        money = Money(Decimal(model.allocated_amount), currency)
        
        props = {
            "payment_id": PaymentId(model.payment_id),
            "invoice_id": InvoiceId(model.invoice_id),
            "organization_id": OrganizationId(model.organization_id),
            "allocated_amount": money,
            "status": model.status,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return PaymentAllocation(PaymentAllocationId(model.id), props)

    async def save(self, allocation: PaymentAllocation) -> None:
        db_model = PaymentAllocationDbModel(
            id=allocation.id.value,
            payment_id=allocation.payment_id.value,
            invoice_id=allocation.invoice_id.value,
            organization_id=allocation.organization_id.value,
            allocated_amount=str(allocation.allocated_amount.amount),
            currency=allocation.allocated_amount.currency.code,
            status=allocation.status,
            created_at=allocation.created_at,
            updated_at=allocation.updated_at
        )
        
        await self._session.merge(db_model)
        await self._session.flush()

        # Transactional Outbox
        for event in allocation.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "payment_id": event.payment_id.value,
                "invoice_id": event.invoice_id.value,
                "allocated_amount": str(event.allocated_amount.amount),
                "occurred_on": event.occurred_on.isoformat()
            }
            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=datetime.utcnow()
            )
            await self._session.merge(outbox_model)
            await self._session.flush()

    async def find_by_id(self, id: PaymentAllocationId) -> Optional[PaymentAllocation]:
        result = await self._session.execute(
            select(PaymentAllocationDbModel).where(PaymentAllocationDbModel.id == id.value)
        )
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_payment_id(self, payment_id: PaymentId) -> List[PaymentAllocation]:
        result = await self._session.execute(
            select(PaymentAllocationDbModel).where(PaymentAllocationDbModel.payment_id == payment_id.value)
        )
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_by_invoice_id(self, invoice_id: InvoiceId, lock: bool = False) -> List[PaymentAllocation]:
        stmt = select(PaymentAllocationDbModel).where(PaymentAllocationDbModel.invoice_id == invoice_id.value)
        if lock:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]


class PostgresReceivableRepository(IReceivableRepository):
    """
    SQLAlchemy-backed implementation of IReceivableRepository.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: ReceivableDbModel) -> Receivable:
        from ...domain.receivable.receivable import Receivable, ReceivablePaymentStatus, ReceivableCollectionStatus, ReceivableStatus
        from ...domain.receivable.value_objects import ReceivableId, CustomerId, InvoiceId, ReceivableReference, CustomerReference, InvoiceReference, ReceivableIdempotencyKey
        from ...domain.receivable.allocation import ReceivableAllocation
        from ...domain.receivable.dunning import DunningState, DunningStage
        from ...domain.organization.value_objects import OrganizationId
        from ...domain.invoice.value_objects import Money, Currency, CurrencyType

        currency_type = CurrencyType.DIGITAL_ASSET if model.currency in ("USDC", "USDT") else CurrencyType.FIAT
        currency = Currency(model.currency, currency_type)
        original_amount = Money(Decimal(model.original_amount), currency)
        allocated_amount = Money(Decimal(model.allocated_amount), currency)
        outstanding_amount = Money(Decimal(model.outstanding_amount), currency)

        # Map allocations relationship
        allocations = []
        if model.allocations:
            for alloc_model in model.allocations:
                alloc = ReceivableAllocation(
                    payment_id=alloc_model.payment_reference,
                    receivable_id=ReceivableId(model.id),
                    allocated_amount=Money(Decimal(alloc_model.allocated_amount), currency),
                    currency=currency,
                    allocated_at=alloc_model.allocated_at,
                    allocation_reference=alloc_model.id
                )
                allocations.append(alloc)

        # Map dunning state
        dunning_state = DunningState(
            stage=DunningStage(model.dunning_stage),
            attempts=model.dunning_attempt_count,
            last_attempt_at=None,
            next_attempt_at=model.next_dunning_at
        )

        from ...domain.receivable.aging import AgingBucket
        props = {
            "organization_id": OrganizationId(model.organization_id),
            "reference": ReceivableReference(model.reference),
            "customer_id": CustomerId(model.customer_id),
            "customer_reference": CustomerReference(model.customer_reference),
            "invoice_id": InvoiceId(model.invoice_id),
            "invoice_reference": InvoiceReference(model.invoice_reference),
            "original_amount": original_amount,
            "allocated_amount": allocated_amount,
            "outstanding_amount": outstanding_amount,
            "due_date": model.due_date,
            "status": ReceivableStatus(model.status),
            "dunning_state": dunning_state,
            "allocations": allocations,
            "idempotency_key": ReceivableIdempotencyKey(model.idempotency_key),
            "last_aging_bucket": AgingBucket(model.aging_bucket),
            "dunning_paused": model.dunning_paused,
            # Mapped legacy fields
            "paid_amount": allocated_amount,
            "payment_status": ReceivablePaymentStatus(model.payment_status),
            "collection_status": ReceivableCollectionStatus(model.collection_status),
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return Receivable(ReceivableId(model.id), props)

    def _to_db(self, aggregate: Receivable) -> ReceivableDbModel:
        from ...domain.receivable.aging import AgingBucket
        from ...domain.shared.clock.clock import Clock
        days = aggregate.calculate_days_overdue(Clock.now())
        aging_bucket_val = AgingBucket.from_days_overdue(days).value

        return ReceivableDbModel(
            id=aggregate.id.value,
            organization_id=aggregate.organization_id.value,
            reference=aggregate.reference.value,
            customer_reference=aggregate.customer_reference.value,
            invoice_reference=aggregate.invoice_reference.value,
            customer_id=aggregate.customer_id.value,
            invoice_id=aggregate.invoice_id.value,
            currency=aggregate.currency.code,
            original_amount=str(aggregate.original_amount.amount),
            allocated_amount=str(aggregate.allocated_amount.amount),
            outstanding_amount=str(aggregate.outstanding_amount.amount),
            due_date=aggregate.due_date,
            status=aggregate.status.value if hasattr(aggregate.status, "value") else aggregate.status,
            aging_bucket=aging_bucket_val,
            dunning_stage=aggregate.dunning_state.stage.value if hasattr(aggregate.dunning_state.stage, "value") else aggregate.dunning_state.stage,
            dunning_attempt_count=aggregate.dunning_state.attempts,
            next_dunning_at=aggregate.dunning_state.next_attempt_at,
            idempotency_key=aggregate.idempotency_key.value,
            dunning_paused=aggregate.dunning_paused,
            # Legacy fields for backward compatibility
            paid_amount=str(aggregate.allocated_amount.amount),
            payment_status=aggregate.payment_status.value if hasattr(aggregate.payment_status, "value") else aggregate.payment_status,
            collection_status=aggregate.collection_status.value if hasattr(aggregate.collection_status, "value") else aggregate.collection_status,
            created_at=aggregate.created_at,
            updated_at=aggregate.updated_at
        )

    async def get_by_id(self, id: ReceivableId, lock: bool = False) -> Optional[Receivable]:
        stmt = select(ReceivableDbModel).where(ReceivableDbModel.id == id.value).options(selectinload(ReceivableDbModel.allocations))
        if lock:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def get_by_reference(self, reference: ReceivableReference) -> Optional[Receivable]:
        stmt = select(ReceivableDbModel).where(ReceivableDbModel.reference == reference.value).options(selectinload(ReceivableDbModel.allocations))
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_invoice(self, invoice_id: InvoiceId) -> Optional[Receivable]:
        stmt = select(ReceivableDbModel).where(ReceivableDbModel.invoice_id == invoice_id.value).options(selectinload(ReceivableDbModel.allocations))
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_customer(self, customer_id: CustomerId) -> List[Receivable]:
        stmt = select(ReceivableDbModel).where(ReceivableDbModel.customer_id == customer_id.value).options(selectinload(ReceivableDbModel.allocations))
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_outstanding(self, organization_id: OrganizationId) -> List[Receivable]:
        from sqlalchemy import cast, Numeric
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0,
            ReceivableDbModel.status != "SETTLED",
            ReceivableDbModel.status != "WRITTEN_OFF"
        ).options(selectinload(ReceivableDbModel.allocations))
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_overdue(self, organization_id: OrganizationId, current_time: datetime) -> List[Receivable]:
        from sqlalchemy import cast, Numeric
        stmt = select(ReceivableDbModel).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0,
            ReceivableDbModel.due_date < current_time,
            ReceivableDbModel.status != "SETTLED",
            ReceivableDbModel.status != "WRITTEN_OFF"
        ).options(selectinload(ReceivableDbModel.allocations))
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def save(self, receivable: Receivable) -> None:
        db_model = self._to_db(receivable)
        try:
            await self._session.merge(db_model)
            # Persist allocations
            from .models import ReceivableAllocationDbModel
            for alloc in receivable.allocations:
                alloc_model = ReceivableAllocationDbModel(
                    id=alloc.allocation_reference,
                    receivable_id=receivable.id.value,
                    payment_reference=alloc.payment_id,
                    allocated_amount=str(alloc.allocated_amount.amount),
                    currency=alloc.currency.code,
                    allocated_at=alloc.allocated_at,
                    organization_id=receivable.organization_id.value
                )
                await self._session.merge(alloc_model)
            await self._session.flush()
        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_receivables_reference" in err_str or "receivables.reference" in err_str or "key (reference)" in err_str:
                raise ValueError("DUPLICATE_RECEIVABLE_REFERENCE")
            elif "uq_receivables_org_idempotency" in err_str or "receivables.idempotency_key" in err_str or "key (organization_id, idempotency_key)" in err_str:
                raise ValueError("DUPLICATE_RECEIVABLE_IDEMPOTENCY")
            raise err

        # Transactional Outbox
        for event in receivable.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "receivable_id": event.receivable_id.value,
                "organization_id": event.organization_id.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            if event.event_name == "ReceivableCreated":
                payload_dict.update({
                    "reference": event.reference.value,
                    "invoice_id": event.invoice_id.value,
                    "invoice_reference": event.invoice_reference.value,
                    "customer_id": event.customer_id.value,
                    "customer_reference": event.customer_reference.value,
                    "original_amount": str(event.original_amount.amount),
                    "currency": event.original_amount.currency.code,
                    "due_date": event.due_date.isoformat()
                })
            elif event.event_name == "ReceivablePartiallyPaid":
                payload_dict.update({
                    "allocated_amount": str(event.allocated_amount.amount),
                    "paid_amount": str(event.paid_amount.amount),
                    "outstanding_amount": str(event.outstanding_amount.amount)
                })
            elif event.event_name in ("ReceivableSettled", "ReceivablePaid"):
                payload_dict.update({
                    "allocated_amount": str(event.allocated_amount.amount),
                    "paid_amount": str(event.paid_amount.amount)
                })
            elif event.event_name == "ReceivableBecameOverdue":
                pass
            elif event.event_name == "ReceivableWrittenOff":
                payload_dict.update({
                    "write_off_reason": event.write_off_reason
                })
            elif event.event_name == "ReceivablePaymentAllocated":
                payload_dict.update({
                    "payment_id": event.payment_id,
                    "allocated_amount": str(event.allocated_amount.amount),
                    "allocation_reference": event.allocation_reference
                })
            elif event.event_name == "ReceivableAgingThresholdReached":
                payload_dict.update({
                    "aging_bucket": event.aging_bucket
                })
            elif event.event_name == "DunningStageChanged":
                payload_dict.update({
                    "previous_stage": event.previous_stage.value,
                    "new_stage": event.new_stage.value
                })
            elif event.event_name == "ReceivableCollectionAttentionRequired":
                pass

            corr_id = current_correlation_id.get()
            caus_id = current_causation_id.get()
            if corr_id or caus_id:
                payload_dict["_metadata"] = {
                    "correlation_id": corr_id,
                    "causation_id": caus_id
                }

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        receivable.clear_domain_events()

    async def delete(self, id: ReceivableId) -> None:
        raise ValueError("Audit Trail Protection: Deleting a financial Receivable record is rejected.")

    # Legacy compatibility methods
    async def find_by_invoice_id(self, invoice_id: InvoiceId) -> Optional[Receivable]:
        return await self.find_by_invoice(invoice_id)

    async def find_by_customer_id(self, customer_id: CustomerId) -> List[Receivable]:
        return await self.find_by_customer(customer_id)

    async def find_by_organization(self, organization_id: OrganizationId) -> List[Receivable]:
        stmt = select(ReceivableDbModel).where(ReceivableDbModel.organization_id == organization_id.value).options(selectinload(ReceivableDbModel.allocations))
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_outstanding_candidates(self, organization_id: OrganizationId, current_time: datetime) -> List[Receivable]:
        from sqlalchemy import cast, Numeric
        result = await self._session.execute(
            select(ReceivableDbModel).where(
                ReceivableDbModel.organization_id == organization_id.value,
                cast(ReceivableDbModel.outstanding_amount, Numeric) > 0,
                ReceivableDbModel.due_date <= current_time
            ).options(selectinload(ReceivableDbModel.allocations))
        )
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def get_aging_summary(self, organization_id: OrganizationId, current_time: datetime) -> dict:
        from sqlalchemy import cast, Numeric, func, case
        from datetime import timedelta
        
        t_current = current_time
        t_30 = current_time - timedelta(days=30)
        t_60 = current_time - timedelta(days=60)
        t_90 = current_time - timedelta(days=90)

        stmt = select(
            func.coalesce(func.sum(case((ReceivableDbModel.due_date >= t_current, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_current) & (ReceivableDbModel.due_date >= t_30), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_30) & (ReceivableDbModel.due_date >= t_60), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case(((ReceivableDbModel.due_date < t_60) & (ReceivableDbModel.due_date >= t_90), cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0),
            func.coalesce(func.sum(case((ReceivableDbModel.due_date < t_90, cast(ReceivableDbModel.outstanding_amount, Numeric)), else_=0)), 0)
        ).where(
            ReceivableDbModel.organization_id == organization_id.value,
            cast(ReceivableDbModel.outstanding_amount, Numeric) > 0
        )

        res = await self._session.execute(stmt)
        row = res.fetchone()
        
        current_val, days_1_30, days_31_60, days_61_90, days_90_plus = row if row else (0, 0, 0, 0, 0)
        
        current_val = Decimal(str(current_val)).quantize(Decimal("0.00"))
        days_1_30 = Decimal(str(days_1_30)).quantize(Decimal("0.00"))
        days_31_60 = Decimal(str(days_31_60)).quantize(Decimal("0.00"))
        days_61_90 = Decimal(str(days_61_90)).quantize(Decimal("0.00"))
        days_90_plus = Decimal(str(days_90_plus)).quantize(Decimal("0.00"))
        
        total_outstanding = current_val + days_1_30 + days_31_60 + days_61_90 + days_90_plus

        return {
            "current": f"{current_val:.2f}",
            "days_1_30": f"{days_1_30:.2f}",
            "days_31_60": f"{days_31_60:.2f}",
            "days_61_90": f"{days_61_90:.2f}",
            "days_90_plus": f"{days_90_plus:.2f}",
            "total_outstanding": f"{total_outstanding:.2f}"
        }


class PostgresSettlementRepository(ISettlementRepository):
    """
    SQLAlchemy-backed implementation of ISettlementRepository.
    Enforces atomic aggregate updates, tenant boundaries, and outbox event publishing.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: SettlementDbModel) -> Settlement:
        from ...domain.invoice.value_objects import Money, Currency, CurrencyType

        currency_type = CurrencyType.DIGITAL_ASSET if model.asset_type == "DIGITAL_ASSET" else CurrencyType.FIAT
        currency = Currency(model.asset_code, currency_type)
        amount = Money(Decimal(model.amount), currency)

        network = SettlementNetwork(model.network)
        dest = SettlementDestination(
            SettlementDestinationType(model.destination_type),
            model.destination_identifier,
            network
        )
        source = SettlementSource(SettlementSourceType.PAYMENT, model.source_payment_id)

        failure = None
        if model.failure_code:
            failure = SettlementFailure(
                SettlementFailureCode(model.failure_code),
                model.failure_message,
                model.failure_retryable
            )

        props = {
            "organization_id": OrganizationId(model.organization_id),
            "reference": SettlementReference(model.reference),
            "asset": SettlementAsset(model.asset_code, model.asset_type, model.asset_decimals),
            "network": network,
            "amount": amount,
            "destination": dest,
            "method": SettlementMethod(model.method),
            "source": source,
            "idempotency_key": SettlementIdempotencyKey(model.idempotency_key),
            "status": SettlementStatus(model.status),
            "failure": failure,
            "transaction_reference": model.transaction_reference,
            "created_at": model.created_at,
            "processing_started_at": model.processing_started_at,
            "submitted_at": model.submitted_at,
            "confirmed_at": model.confirmed_at,
            "completed_at": model.completed_at,
            "cancelled_at": model.cancelled_at,
            "updated_at": model.updated_at
        }
        return Settlement(SettlementId(model.id), props)

    def _to_db(self, aggregate: Settlement) -> SettlementDbModel:
        failure_code = None
        failure_message = None
        failure_retryable = None
        if aggregate.failure:
            failure_code = aggregate.failure.code.value
            failure_message = aggregate.failure.message
            failure_retryable = aggregate.failure.retryable

        processing_started_at = aggregate._props.get("processing_started_at")
        if aggregate.status == SettlementStatus.PROCESSING and not processing_started_at:
            processing_started_at = aggregate.updated_at

        confirmed_at = aggregate._props.get("confirmed_at")
        if aggregate.status == SettlementStatus.CONFIRMED and not confirmed_at:
            confirmed_at = aggregate.updated_at

        cancelled_at = aggregate._props.get("cancelled_at")
        if aggregate.status == SettlementStatus.CANCELLED and not cancelled_at:
            cancelled_at = aggregate.updated_at

        return SettlementDbModel(
            id=aggregate.id.value,
            organization_id=aggregate.organization_id.value,
            reference=aggregate.reference.value,
            source_payment_id=aggregate.source.payment_id,
            asset_code=aggregate.asset.code,
            asset_type=aggregate.asset.type,
            asset_decimals=aggregate.asset.decimals,
            network=aggregate.network.value,
            destination_type=aggregate.destination.type.value,
            destination_identifier=aggregate.destination.identifier,
            method=aggregate.method.value,
            amount=str(aggregate.amount.amount),
            status=aggregate.status.value,
            failure_code=failure_code,
            failure_message=failure_message,
            failure_retryable=failure_retryable,
            idempotency_key=aggregate.idempotency_key.value,
            transaction_reference=aggregate.transaction_reference,
            created_at=aggregate.created_at,
            processing_started_at=processing_started_at,
            submitted_at=aggregate.submitted_at,
            confirmed_at=confirmed_at,
            completed_at=aggregate.completed_at,
            cancelled_at=cancelled_at,
            updated_at=aggregate.updated_at
        )

    async def get_by_id(self, id: SettlementId, lock: bool = False) -> Optional[Settlement]:
        stmt = select(SettlementDbModel).where(SettlementDbModel.id == id.value)
        if lock:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_reference(self, reference: SettlementReference) -> Optional[Settlement]:
        stmt = select(SettlementDbModel).where(SettlementDbModel.reference == reference.value)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_payment_id(self, payment_id: str) -> List[Settlement]:
        stmt = select(SettlementDbModel).where(SettlementDbModel.source_payment_id == payment_id)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_by_organization(self, organization_id: OrganizationId) -> List[Settlement]:
        stmt = select(SettlementDbModel).where(SettlementDbModel.organization_id == organization_id.value)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_by_idempotency_key(self, organization_id: OrganizationId, idempotency_key: SettlementIdempotencyKey) -> Optional[Settlement]:
        stmt = select(SettlementDbModel).where(
            SettlementDbModel.organization_id == organization_id.value,
            SettlementDbModel.idempotency_key == idempotency_key.value
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_pending(self) -> List[Settlement]:
        stmt = select(SettlementDbModel).where(SettlementDbModel.status == SettlementStatus.PENDING.value)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def save(self, settlement: Settlement) -> None:
        db_model = self._to_db(settlement)
        try:
            await self._session.merge(db_model)
            await self._session.flush()
        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_settlements_reference" in err_str or "settlements.reference" in err_str or "key (reference)" in err_str:
                raise ValueError("DUPLICATE_SETTLEMENT_REFERENCE")
            elif "uq_settlements_org_idempotency" in err_str or "settlements.idempotency_key" in err_str or "idempotency_key" in err_str:
                raise ValueError("DUPLICATE_SETTLEMENT_IDEMPOTENCY")
            raise err

        # Persist domain events to transaction outbox
        for event in settlement.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "settlement_id": event.settlement_id.value,
                "organization_id": settlement.organization_id.value,
                "reference": settlement.reference.value,
                "status": settlement.status.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            if event.event_name == "SettlementCreated":
                payload_dict.update({
                    "amount": str(settlement.amount.amount),
                    "currency": settlement.amount.currency.code,
                    "method": settlement.method.value,
                    "network": settlement.network.value,
                    "destination": settlement.destination.identifier
                })
            elif event.event_name == "SettlementSubmitted":
                payload_dict["transaction_reference"] = event.transaction_reference
            elif event.event_name == "SettlementFailed":
                payload_dict.update({
                    "failure_code": event.failure.code.value,
                    "failure_message": event.failure.message,
                    "failure_retryable": event.failure.retryable
                })

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        settlement.clear_domain_events()

    async def delete(self, id: SettlementId) -> None:
        raise ValueError("Audit Trail Protection: Deleting a financial Settlement record is rejected.")


class PostgresNotificationRepository(INotificationRepository):
    """
    SQLAlchemy-backed implementation of INotificationRepository.
    Integrates transactional outbox patterns to persist domain events atomically.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: NotificationDbModel) -> Notification:
        failure = None
        if model.failure_code:
            failure = NotificationFailure(
                code=NotificationFailureCode(model.failure_code),
                message=model.failure_message,
                retryable=model.failure_retryable
            )

        props = {
            "organization_id": OrganizationId(model.organization_id),
            "reference": NotificationReference(model.reference),
            "notification_type": NotificationType(model.notification_type),
            "recipient": NotificationRecipient(NotificationChannel(model.channel), model.recipient),
            "channel": NotificationChannel(model.channel),
            "subject": model.subject,
            "template_id": NotificationTemplateId(model.template_id),
            "payload": NotificationPayload(json.loads(model.payload)),
            "priority": NotificationPriority(model.priority),
            "status": NotificationStatus(model.status),
            "idempotency_key": NotificationIdempotencyKey(model.idempotency_key),
            "source_event_id": model.source_event_id,
            "source_event_type": model.source_event_type,
            "failure": failure,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "delivered_at": model.delivered_at,
            "processing_started_at": model.processing_started_at,
            "scheduled_at": model.scheduled_at,
            "retry_count": model.retry_count,
            "next_retry_at": model.next_retry_at,
            "max_attempts": model.max_attempts,
            "replay_count": model.replay_count
        }
        return Notification(NotificationId(model.id), props)

    def _to_db(self, aggregate: Notification) -> NotificationDbModel:
        failure_code = None
        failure_message = None
        failure_retryable = None
        if aggregate.failure:
            failure_code = aggregate.failure.code.value
            failure_message = aggregate.failure.message
            failure_retryable = aggregate.failure.retryable

        return NotificationDbModel(
            id=aggregate.id.value,
            organization_id=aggregate.organization_id.value,
            reference=aggregate.reference.value,
            notification_type=aggregate.notification_type.value,
            channel=aggregate.channel.value,
            recipient=aggregate.recipient.destination,
            subject=aggregate.subject,
            template_id=aggregate.template_id.value,
            payload=json.dumps(dict(aggregate.payload.data)),
            priority=aggregate.priority.value,
            status=aggregate.status.value,
            failure_code=failure_code,
            failure_message=failure_message,
            failure_retryable=failure_retryable,
            idempotency_key=aggregate.idempotency_key.value,
            source_event_id=aggregate.source_event_id,
            source_event_type=aggregate.source_event_type,
            created_at=aggregate.created_at,
            updated_at=aggregate.updated_at,
            delivered_at=aggregate.delivered_at,
            processing_started_at=aggregate.processing_started_at,
            scheduled_at=aggregate.scheduled_at,
            retry_count=aggregate.retry_count,
            next_retry_at=aggregate.next_retry_at,
            max_attempts=aggregate.max_attempts,
            replay_count=aggregate.replay_count
        )

    async def get_by_id(self, id: NotificationId, lock: bool = False) -> Optional[Notification]:
        stmt = select(NotificationDbModel).where(NotificationDbModel.id == id.value)
        if lock:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_reference(self, reference: NotificationReference) -> Optional[Notification]:
        stmt = select(NotificationDbModel).where(NotificationDbModel.reference == reference.value)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_organization(self, organization_id: OrganizationId) -> List[Notification]:
        stmt = select(NotificationDbModel).where(NotificationDbModel.organization_id == organization_id.value)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_by_idempotency_key(
        self,
        organization_id: OrganizationId,
        idempotency_key: NotificationIdempotencyKey
    ) -> Optional[Notification]:
        stmt = select(NotificationDbModel).where(
            NotificationDbModel.organization_id == organization_id.value,
            NotificationDbModel.idempotency_key == idempotency_key.value
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_source_event(self, source_event_id: str, source_event_type: str) -> List[Notification]:
        stmt = select(NotificationDbModel).where(
            NotificationDbModel.source_event_id == source_event_id,
            NotificationDbModel.source_event_type == source_event_type
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def find_pending(self, current_time: Optional[datetime] = None) -> List[Notification]:
        now = current_time or Clock.now()
        stmt = select(NotificationDbModel).where(
            ((NotificationDbModel.status == "PENDING") & (NotificationDbModel.scheduled_at <= now)) |
            ((NotificationDbModel.status == "RETRYING") & (NotificationDbModel.next_retry_at <= now))
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_aggregate(m) for m in models]

    async def save(self, notification: Notification) -> None:
        db_model = self._to_db(notification)
        try:
            await self._session.merge(db_model)
            await self._session.flush()
        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_notifications_reference" in err_str or "notifications.reference" in err_str or "key (reference)" in err_str:
                raise ValueError("DUPLICATE_NOTIFICATION_REFERENCE")
            elif "uq_notifications_org_idempotency" in err_str or "notifications.idempotency_key" in err_str or "key (organization_id, idempotency_key)" in err_str:
                raise ValueError("DUPLICATE_NOTIFICATION_IDEMPOTENCY")
            raise err

        # Persist domain events to outbox
        for event in notification.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "notification_id": event.notification_id.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            if event.event_name == "NotificationCreated":
                payload_dict.update({
                    "organization_id": event.organization_id.value,
                    "reference": notification.reference.value,
                    "notification_type": notification.notification_type.value,
                    "channel": notification.channel.value,
                    "recipient": notification.recipient.destination,
                    "subject": notification.subject,
                    "template_id": notification.template_id.value,
                    "payload": dict(notification.payload.data),
                    "priority": notification.priority.value
                })
            elif event.event_name == "NotificationDeliveryFailed":
                payload_dict.update({
                    "failure_code": event.failure.code.value,
                    "failure_message": event.failure.message,
                    "failure_retryable": event.failure.retryable
                })

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        notification.clear_domain_events()

    async def delete(self, id: NotificationId) -> None:
        raise ValueError("Audit Trail Protection: Deleting a operational Notification record is rejected.")


class PostgresNotificationTemplateRepository(INotificationTemplateRepository):
    """
    SQLAlchemy-backed implementation of INotificationTemplateRepository.
    Enforces bounded-context template management with audit protections.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: NotificationTemplateDbModel) -> NotificationTemplate:
        props = {
            "organization_id": OrganizationId(model.organization_id),
            "reference": TemplateReference(model.reference),
            "version": TemplateVersion(model.version),
            "content": TemplateContent(model.content),
            "status": TemplateStatus(model.status),
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return NotificationTemplate(TemplateId(model.id), props)

    def _to_db(self, aggregate: NotificationTemplate) -> NotificationTemplateDbModel:
        return NotificationTemplateDbModel(
            id=aggregate.id.value,
            organization_id=aggregate.organization_id.value,
            reference=aggregate.reference.value,
            version=aggregate.version.value,
            content=aggregate.content.value,
            status=aggregate.status.value,
            created_at=aggregate.created_at,
            updated_at=aggregate.updated_at
        )

    async def get_by_id(self, id: TemplateId) -> Optional[NotificationTemplate]:
        stmt = select(NotificationTemplateDbModel).where(NotificationTemplateDbModel.id == id.value)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def get_active_version(
        self,
        organization_id: OrganizationId,
        reference: TemplateReference
    ) -> Optional[NotificationTemplate]:
        ref_str = reference.value if hasattr(reference, "value") else reference
        stmt = select(NotificationTemplateDbModel).where(
            NotificationTemplateDbModel.organization_id == organization_id.value,
            NotificationTemplateDbModel.reference == ref_str,
            NotificationTemplateDbModel.status == "ACTIVE"
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def find_by_reference_and_version(
        self,
        organization_id: OrganizationId,
        reference: TemplateReference,
        version: TemplateVersion
    ) -> Optional[NotificationTemplate]:
        ref_str = reference.value if hasattr(reference, "value") else reference
        ver_str = version.value if hasattr(version, "value") else version
        stmt = select(NotificationTemplateDbModel).where(
            NotificationTemplateDbModel.organization_id == organization_id.value,
            NotificationTemplateDbModel.reference == ref_str,
            NotificationTemplateDbModel.version == ver_str
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def save(self, template: NotificationTemplate) -> None:
        db_model = self._to_db(template)
        try:
            await self._session.merge(db_model)
            await self._session.flush()
        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_templates_org_ref_version" in err_str or "notification_templates.version" in err_str or "key (organization_id, reference, version)" in err_str:
                raise ValueError("DUPLICATE_TEMPLATE_VERSION")
            elif "uq_active_template_per_org_ref" in err_str or "active_template" in err_str or "notification_templates.organization_id, notification_templates.reference" in err_str:
                raise ValueError("DUPLICATE_ACTIVE_TEMPLATE")
            raise err

    async def delete(self, id: TemplateId) -> None:
        raise ValueError("Audit Trail Protection: Deleting a operational template record is rejected.")


class PostgresNotificationPreferenceRepository(INotificationPreferenceRepository):
    """
    SQLAlchemy-backed implementation of INotificationPreferenceRepository.
    Enforces bounded-context preference management for organizations.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_aggregate(self, model: NotificationPreferenceDbModel) -> NotificationPreference:
        props = {
            "organization_id": OrganizationId(model.organization_id),
            "event_type": model.event_type,
            "channel": NotificationChannel(model.channel),
            "is_enabled": model.is_enabled,
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return NotificationPreference(PreferenceId(model.id), props)

    def _to_db(self, aggregate: NotificationPreference) -> NotificationPreferenceDbModel:
        return NotificationPreferenceDbModel(
            id=aggregate.id.value,
            organization_id=aggregate.organization_id.value,
            event_type=aggregate.event_type,
            channel=aggregate.channel.value,
            is_enabled=aggregate.is_enabled,
            created_at=aggregate.created_at,
            updated_at=aggregate.updated_at
        )

    async def find_preference(
        self,
        organization_id: OrganizationId,
        event_type: str,
        channel: NotificationChannel
    ) -> Optional[NotificationPreference]:
        stmt = select(NotificationPreferenceDbModel).where(
            NotificationPreferenceDbModel.organization_id == organization_id.value,
            NotificationPreferenceDbModel.event_type == event_type,
            NotificationPreferenceDbModel.channel == channel.value
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_aggregate(model) if model else None

    async def save(self, preference: NotificationPreference) -> None:
        db_model = self._to_db(preference)
        try:
            await self._session.merge(db_model)
            await self._session.flush()
        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_pref_org_event_channel" in err_str or "notification_preferences.channel" in err_str or "key (organization_id, event_type, channel)" in err_str:
                raise ValueError("DUPLICATE_PREFERENCE")
            raise err


from ...domain.workflow import (
    Workflow,
    WorkflowId,
    WorkflowReference,
    WorkflowName,
    WorkflowStatus,
    WorkflowVersion,
    WorkflowIdempotencyKey,
    WorkflowTrigger,
    ConditionOperator,
    Condition,
    LogicalOperator,
    ConditionGroup,
    ActionType,
    WorkflowAction,
    WorkflowDefinition,
    IWorkflowRepository
)


class PostgresWorkflowRepository(IWorkflowRepository):
    """
    SQLAlchemy-backed implementation of IWorkflowRepository.
    Integrates transactional outbox patterns to persist domain events atomically.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _serialize_conditions(self, group: ConditionGroup) -> dict:
        serialized_conds = []
        for cond in group.conditions:
            if isinstance(cond, Condition):
                serialized_conds.append({
                    "type": "condition",
                    "field": cond.field,
                    "operator": cond.operator.value,
                    "value": cond.value
                })
            elif isinstance(cond, ConditionGroup):
                serialized_conds.append(self._serialize_conditions(cond))
        return {
            "type": "group",
            "operator": group.operator.value,
            "conditions": serialized_conds
        }

    def _deserialize_conditions(self, data: dict) -> ConditionGroup:
        conditions = []
        for item in data["conditions"]:
            if item["type"] == "condition":
                conditions.append(Condition(
                    field=item["field"],
                    operator=ConditionOperator(item["operator"]),
                    value=item.get("value")
                ))
            elif item["type"] == "group":
                conditions.append(self._deserialize_conditions(item))
        return ConditionGroup(
            operator=LogicalOperator(data["operator"]),
            conditions=conditions
        )

    def _to_aggregate(self, model: WorkflowDbModel, versions: List[WorkflowVersionDbModel]) -> Workflow:
        definitions = {}
        for v_model in versions:
            trigger_data = json.loads(v_model.trigger)
            trigger = WorkflowTrigger(trigger_data["event_type"])
            conditions = self._deserialize_conditions(json.loads(v_model.conditions))
            actions_data = json.loads(v_model.actions)
            actions = [WorkflowAction(ActionType(act["action_type"]), act["parameters"]) for act in actions_data]
            
            definitions[v_model.version] = WorkflowDefinition(
                version=WorkflowVersion(v_model.version),
                trigger=trigger,
                conditions=conditions,
                actions=actions,
                created_at=v_model.created_at
            )

        props = {
            "organization_id": OrganizationId(model.organization_id),
            "reference": WorkflowReference(model.reference),
            "name": WorkflowName(model.name),
            "description": model.description,
            "status": WorkflowStatus(model.status),
            "definitions": definitions,
            "active_version_number": model.active_version_number,
            "idempotency_key": WorkflowIdempotencyKey(model.idempotency_key),
            "created_at": model.created_at,
            "updated_at": model.updated_at
        }
        return Workflow(WorkflowId(model.id), props)

    def _to_db(self, aggregate: Workflow) -> WorkflowDbModel:
        return WorkflowDbModel(
            id=aggregate.id.value,
            organization_id=aggregate.organization_id.value,
            reference=aggregate.reference.value,
            name=aggregate.name.value,
            description=aggregate.description,
            status=aggregate.status.value,
            active_version_number=aggregate.active_version_number,
            idempotency_key=aggregate.idempotency_key.value,
            created_at=aggregate.created_at,
            updated_at=aggregate.updated_at
        )

    async def get_by_id(self, id: WorkflowId, lock: bool = False) -> Optional[Workflow]:
        stmt = select(WorkflowDbModel).where(WorkflowDbModel.id == id.value)
        if lock:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None

        # Fetch all versions
        v_stmt = select(WorkflowVersionDbModel).where(WorkflowVersionDbModel.workflow_id == id.value)
        v_result = await self._session.execute(v_stmt)
        versions = v_result.scalars().all()

        return self._to_aggregate(model, versions)

    async def find_by_reference(self, reference: WorkflowReference) -> Optional[Workflow]:
        stmt = select(WorkflowDbModel).where(WorkflowDbModel.reference == reference.value)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None

        v_stmt = select(WorkflowVersionDbModel).where(WorkflowVersionDbModel.workflow_id == model.id)
        v_result = await self._session.execute(v_stmt)
        versions = v_result.scalars().all()

        return self._to_aggregate(model, versions)

    async def find_by_organization(self, organization_id: OrganizationId) -> List[Workflow]:
        stmt = select(WorkflowDbModel).where(WorkflowDbModel.organization_id == organization_id.value)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        if not models:
            return []

        wf_ids = [m.id for m in models]
        v_stmt = select(WorkflowVersionDbModel).where(WorkflowVersionDbModel.workflow_id.in_(wf_ids))
        v_result = await self._session.execute(v_stmt)
        versions = v_result.scalars().all()

        # Group versions by workflow_id
        versions_by_wf = {}
        for v in versions:
            versions_by_wf.setdefault(v.workflow_id, []).append(v)

        return [self._to_aggregate(m, versions_by_wf.get(m.id, [])) for m in models]

    async def find_by_status(
        self,
        organization_id: OrganizationId,
        status: WorkflowStatus
    ) -> List[Workflow]:
        stmt = select(WorkflowDbModel).where(
            WorkflowDbModel.organization_id == organization_id.value,
            WorkflowDbModel.status == status.value
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        if not models:
            return []

        wf_ids = [m.id for m in models]
        v_stmt = select(WorkflowVersionDbModel).where(WorkflowVersionDbModel.workflow_id.in_(wf_ids))
        v_result = await self._session.execute(v_stmt)
        versions = v_result.scalars().all()

        versions_by_wf = {}
        for v in versions:
            versions_by_wf.setdefault(v.workflow_id, []).append(v)

        return [self._to_aggregate(m, versions_by_wf.get(m.id, [])) for m in models]

    async def find_by_idempotency_key(
        self,
        organization_id: OrganizationId,
        idempotency_key: WorkflowIdempotencyKey
    ) -> Optional[Workflow]:
        stmt = select(WorkflowDbModel).where(
            WorkflowDbModel.organization_id == organization_id.value,
            WorkflowDbModel.idempotency_key == idempotency_key.value
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None

        v_stmt = select(WorkflowVersionDbModel).where(WorkflowVersionDbModel.workflow_id == model.id)
        v_result = await self._session.execute(v_stmt)
        versions = v_result.scalars().all()

        return self._to_aggregate(model, versions)

    async def find_active(self) -> List[Workflow]:
        stmt = select(WorkflowDbModel).where(WorkflowDbModel.status == "ACTIVE")
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        if not models:
            return []

        wf_ids = [m.id for m in models]
        v_stmt = select(WorkflowVersionDbModel).where(WorkflowVersionDbModel.workflow_id.in_(wf_ids))
        v_result = await self._session.execute(v_stmt)
        versions = v_result.scalars().all()

        versions_by_wf = {}
        for v in versions:
            versions_by_wf.setdefault(v.workflow_id, []).append(v)

        return [self._to_aggregate(m, versions_by_wf.get(m.id, [])) for m in models]

    async def find_by_trigger(self, trigger_event: str) -> List[Workflow]:
        # Retrieve all active workflows and filter in memory by trigger event
        active_workflows = await self.find_active()
        matching = []
        for wf in active_workflows:
            if wf.active_version_number is not None:
                active_def = wf.definitions.get(wf.active_version_number)
                if active_def and active_def.trigger.event_type == trigger_event:
                    matching.append(wf)
        return matching

    async def save(self, workflow: Workflow) -> None:
        db_model = self._to_db(workflow)
        try:
            await self._session.merge(db_model)
            # Merge definitions
            for definition in workflow.definitions.values():
                v_model = WorkflowVersionDbModel(
                    id=f"{workflow.id.value}_{definition.version.value}",
                    workflow_id=workflow.id.value,
                    version=definition.version.value,
                    trigger=json.dumps({"event_type": definition.trigger.event_type}),
                    conditions=json.dumps(self._serialize_conditions(definition.conditions)),
                    actions=json.dumps([{"action_type": action.action_type.value, "parameters": dict(action.parameters)} for action in definition.actions]),
                    created_at=definition.created_at
                )
                await self._session.merge(v_model)
            await self._session.flush()
        except IntegrityError as err:
            err_str = str(err).lower()
            if "uq_workflows_reference" in err_str or "workflows.reference" in err_str or "key (reference)" in err_str:
                raise ValueError("DUPLICATE_WORKFLOW_REFERENCE")
            elif "uq_workflows_org_idempotency" in err_str or "workflows.idempotency_key" in err_str or "key (organization_id, idempotency_key)" in err_str:
                raise ValueError("DUPLICATE_WORKFLOW_IDEMPOTENCY")
            elif "uq_workflow_versions_wf_version" in err_str or "workflow_versions.version" in err_str or "key (workflow_id, version)" in err_str:
                raise ValueError("DUPLICATE_WORKFLOW_VERSION")
            raise err

        # Persist domain events to outbox
        for event in workflow.domain_events:
            event_id = str(uuid4())
            payload_dict = {
                "workflow_id": event.workflow_id.value,
                "occurred_on": event.occurred_on.isoformat()
            }
            if event.event_name == "WorkflowCreated":
                payload_dict.update({
                    "organization_id": event.organization_id.value,
                    "reference": workflow.reference.value,
                    "name": workflow.name.value,
                    "status": workflow.status.value,
                    "idempotency_key": workflow.idempotency_key.value,
                    "created_at": workflow.created_at.isoformat()
                })
            elif event.event_name in ("WorkflowVersionCreated", "WorkflowVersionActivated"):
                payload_dict.update({
                    "version": event.version.value
                })

            outbox_model = OutboxEventDbModel(
                id=event_id,
                event_type=event.event_name,
                payload=json.dumps(payload_dict),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        workflow.clear_domain_events()

    async def delete(self, id: WorkflowId) -> None:
        raise ValueError("Audit Trail Protection: Deleting a Workflow record is rejected.")


from ...domain.workflow.repositories import IWorkflowExecutionRepository
from ...domain.workflow.execution import WorkflowExecution, WorkflowExecutionId
from .models import WorkflowExecutionDbModel, WorkflowCursorDbModel

class PostgresWorkflowExecutionRepository(IWorkflowExecutionRepository):
    """
    SQLAlchemy-backed implementation of IWorkflowExecutionRepository.
    Manages persistence of workflow execution history and cursor bookmarks.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_domain(self, db_model: WorkflowExecutionDbModel) -> WorkflowExecution:
        action_results = json.loads(db_model.action_results) if db_model.action_results else []
        props = {
            "workflow_id": WorkflowId(db_model.workflow_id),
            "version": WorkflowVersion(db_model.version),
            "source_event_id": db_model.source_event_id,
            "source_event_type": db_model.source_event_type,
            "status": db_model.status,
            "started_at": db_model.started_at,
            "completed_at": db_model.completed_at,
            "failure_reason": db_model.failure_reason,
            "action_results": action_results,
            "retry_count": db_model.retry_count,
            "max_attempts": db_model.max_attempts,
            "next_retry_at": db_model.next_retry_at,
            "failure_classification": db_model.failure_classification,
            "dead_lettered_at": db_model.dead_lettered_at
        }
        return WorkflowExecution(WorkflowExecutionId(db_model.id), props)

    def _to_db(self, domain: WorkflowExecution) -> WorkflowExecutionDbModel:
        return WorkflowExecutionDbModel(
            id=domain.id.value,
            workflow_id=domain.workflow_id.value,
            version=domain.version.value,
            source_event_id=domain.source_event_id,
            source_event_type=domain.source_event_type,
            status=domain.status,
            started_at=domain.started_at,
            completed_at=domain.completed_at,
            failure_reason=domain.failure_reason,
            action_results=json.dumps(domain.action_results),
            retry_count=domain.retry_count,
            max_attempts=domain.max_attempts,
            next_retry_at=domain.next_retry_at,
            failure_classification=domain.failure_classification,
            dead_lettered_at=domain.dead_lettered_at
        )

    async def get_by_id(self, id: WorkflowExecutionId) -> Optional[WorkflowExecution]:
        stmt = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.id == id.value)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None
        return self._to_domain(model)

    async def find_execution(
        self,
        workflow_id: WorkflowId,
        version: WorkflowVersion,
        source_event_id: str
    ) -> Optional[WorkflowExecution]:
        stmt = select(WorkflowExecutionDbModel).where(
            WorkflowExecutionDbModel.workflow_id == workflow_id.value,
            WorkflowExecutionDbModel.version == version.value,
            WorkflowExecutionDbModel.source_event_id == source_event_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None
        return self._to_domain(model)

    async def save(self, execution: WorkflowExecution) -> None:
        db_model = self._to_db(execution)
        await self._session.merge(db_model)

        for event in execution.domain_events:
            outbox_model = OutboxEventDbModel(
                id=str(uuid4()),
                event_type=event.__class__.__name__,
                payload=json.dumps({
                    "execution_id": str(execution.id.value),
                    "workflow_id": str(execution.workflow_id.value),
                    "status": execution.status,
                    "event_type": event.__class__.__name__
                }),
                processed=False,
                created_at=event.occurred_on
            )
            self._session.add(outbox_model)

        execution.clear_domain_events()

    async def get_cursor(self, consumer_name: str) -> Optional[datetime]:
        stmt = select(WorkflowCursorDbModel).where(WorkflowCursorDbModel.consumer_name == consumer_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None
        return model.last_event_created_at

    async def save_cursor(self, consumer_name: str, last_event_created_at: datetime) -> None:
        db_model = WorkflowCursorDbModel(
            consumer_name=consumer_name,
            last_event_created_at=last_event_created_at
        )
        await self._session.merge(db_model)


from ...domain.receivable.repositories import ICollectionActionRepository
from ...domain.receivable.collection_action import CollectionAction, CollectionActionId
from ...domain.receivable.value_objects import ReceivableId
from ...domain.organization.value_objects import OrganizationId
from .models import ReceivableCollectionActionDbModel

class PostgresCollectionActionRepository(ICollectionActionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_domain(self, model: ReceivableCollectionActionDbModel) -> CollectionAction:
        return CollectionAction.create(
            id=CollectionActionId(model.id),
            organization_id=OrganizationId(model.organization_id),
            receivable_id=ReceivableId(model.receivable_id),
            action_type=model.action_type,
            scheduled_at=model.scheduled_at,
            idempotency_key=model.idempotency_key,
            status=model.status,
            attempt_count=model.attempt_count,
            next_retry_at=model.next_retry_at,
            created_at=model.created_at,
            processed_at=model.processed_at,
            error_message=model.error_message,
            max_attempts=model.max_attempts,
            dead_lettered_at=model.dead_lettered_at,
            failure_classification=model.failure_classification
        )

    def _to_db(self, entity: CollectionAction) -> ReceivableCollectionActionDbModel:
        return ReceivableCollectionActionDbModel(
            id=entity.id.value,
            organization_id=entity.organization_id.value,
            receivable_id=entity.receivable_id.value,
            action_type=entity.action_type,
            scheduled_at=entity.scheduled_at,
            status=entity.status,
            attempt_count=entity.attempt_count,
            next_retry_at=entity.next_retry_at,
            idempotency_key=entity.idempotency_key,
            created_at=entity.created_at,
            processed_at=entity.processed_at,
            error_message=entity.error_message,
            max_attempts=entity.max_attempts,
            dead_lettered_at=entity.dead_lettered_at,
            failure_classification=entity.failure_classification
        )

    async def save(self, action: CollectionAction) -> None:
        db_model = self._to_db(action)
        await self._session.merge(db_model)

    async def find_by_id(self, action_id: CollectionActionId) -> Optional[CollectionAction]:
        stmt = select(ReceivableCollectionActionDbModel).where(ReceivableCollectionActionDbModel.id == action_id.value)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None
        return self._to_domain(model)

    async def claim_pending_actions(self, limit: int, current_time: datetime) -> List[CollectionAction]:
        current_time_naive = current_time.replace(tzinfo=None) if current_time.tzinfo else current_time
        stmt = (
            select(ReceivableCollectionActionDbModel)
            .where(
                (
                    (ReceivableCollectionActionDbModel.status == "PENDING") &
                    (ReceivableCollectionActionDbModel.scheduled_at <= current_time_naive)
                ) |
                (
                    (ReceivableCollectionActionDbModel.status == "RETRYING") &
                    (ReceivableCollectionActionDbModel.next_retry_at <= current_time_naive)
                )
            )
            .limit(limit)
        )
        dialect_name = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect_name != "sqlite":
            stmt = stmt.with_for_update(skip_locked=True)

        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

    async def find_by_idempotency_key(self, organization_id: OrganizationId, idempotency_key: str) -> Optional[CollectionAction]:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.organization_id == organization_id.value,
            ReceivableCollectionActionDbModel.idempotency_key == idempotency_key
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None
        return self._to_domain(model)

    async def cancel_pending_actions(self, receivable_id: ReceivableId) -> None:
        stmt = select(ReceivableCollectionActionDbModel).where(
            ReceivableCollectionActionDbModel.receivable_id == receivable_id.value,
            ReceivableCollectionActionDbModel.status.in_(["PENDING", "RETRYING", "PROCESSING"])
        )
        res = await self._session.execute(stmt)
        actions = res.scalars().all()
        for action in actions:
            action.status = "CANCELLED"





