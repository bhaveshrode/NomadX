from datetime import datetime
from decimal import Decimal
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...domain.settlement_reconciliation.source_transaction import SourceTransaction
from .models import PaymentDbModel


class PostgresSourceTransactionRepository:
    """
    SQLAlchemy-backed implementation to load external source transactions (e.g. Payments)
    within a specific tenant boundary and settlement period.
    """
    def __init__(self, session: AsyncSession):
        self._session = session

    async def find_by_period(
        self,
        organization_id: OrganizationId,
        period_start: datetime,
        period_end: datetime
    ) -> List[SourceTransaction]:
        p_start = period_start.replace(tzinfo=None)
        p_end = period_end.replace(tzinfo=None)

        stmt = (
            select(PaymentDbModel)
            .where(
                PaymentDbModel.organization_id == organization_id.value,
                PaymentDbModel.observed_at >= p_start,
                PaymentDbModel.observed_at <= p_end,
                PaymentDbModel.status == "CONFIRMED"
            )
        )
        res = await self._session.execute(stmt)
        models = res.scalars().all()

        return [
            SourceTransaction(
                id=m.id,
                organization_id=m.organization_id,
                transaction_reference=m.transaction_reference,
                amount=Decimal(m.observed_amount),
                currency_code=m.asset_code,
                observed_at=m.observed_at,
                type="PAYMENT",
                fees=Decimal("0.00"),
                net_amount=Decimal(m.observed_amount)
            )
            for m in models
        ]
