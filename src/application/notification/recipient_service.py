from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...domain.notification.value_objects import NotificationChannel
from ...infrastructure.database.models import (
    InvoiceDbModel,
    ReceivableDbModel,
    PaymentAllocationDbModel,
    OrganizationDbModel,
    UserDbModel
)


class IRecipientResolver(ABC):
    """
    Interface/Port for resolving recipient details without coupling
    the Notification bounded context directly to internals of other contexts.
    """
    @abstractmethod
    async def resolve_recipient(
        self,
        session: AsyncSession,
        org_id: OrganizationId,
        recipient_type: str,
        channel: NotificationChannel,
        context_ids: Dict[str, Any]
    ) -> Optional[str]:
        """Resolves target recipient email/destination address per channel."""
        pass


class RecipientResolver(IRecipientResolver):
    """
    Concrete implementation of IRecipientResolver that queries database models
    to fetch customer billing emails, organization owner emails, or webhook URLs.
    """
    async def resolve_recipient(
        self,
        session: AsyncSession,
        org_id: OrganizationId,
        recipient_type: str,
        channel: NotificationChannel,
        context_ids: Dict[str, Any]
    ) -> Optional[str]:
        if channel == NotificationChannel.WEBHOOK:
            # Webhook recipient must be a valid HTTPS URL
            return f"https://api.nomadx.com/orgs/{org_id.value}/webhooks"

        # Email resolution path
        if recipient_type == "MERCHANT":
            # Find organization owner user email
            stmt = select(UserDbModel.email).join(
                OrganizationDbModel, OrganizationDbModel.owner_id == UserDbModel.id
            ).where(OrganizationDbModel.id == org_id.value)
            res = await session.execute(stmt)
            email = res.scalar_one_or_none()
            return email or "merchant@example.com"

        elif recipient_type == "CUSTOMER":
            # 1. Check invoice_id
            invoice_id = context_ids.get("invoice_id")
            if invoice_id:
                stmt = select(InvoiceDbModel.billing_email).where(InvoiceDbModel.id == invoice_id)
                res = await session.execute(stmt)
                email = res.scalar_one_or_none()
                if email:
                    return email

            # 2. Check receivable_id
            receivable_id = context_ids.get("receivable_id")
            if receivable_id:
                stmt = select(InvoiceDbModel.billing_email).join(
                    ReceivableDbModel, ReceivableDbModel.invoice_id == InvoiceDbModel.id
                ).where(ReceivableDbModel.id == receivable_id)
                res = await session.execute(stmt)
                email = res.scalar_one_or_none()
                if email:
                    return email

            # 3. Check payment_id
            payment_id = context_ids.get("payment_id")
            if payment_id:
                stmt = select(InvoiceDbModel.billing_email).join(
                    PaymentAllocationDbModel, PaymentAllocationDbModel.invoice_id == InvoiceDbModel.id
                ).where(PaymentAllocationDbModel.payment_id == payment_id)
                res = await session.execute(stmt)
                email = res.scalar_one_or_none()
                if email:
                    return email

            return "customer@example.com"

        return None
