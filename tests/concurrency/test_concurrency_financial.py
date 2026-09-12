import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    OutboxEventDbModel,
    InvoiceDbModel
)
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.invoice import InvoiceId
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor

async def _seed_invoice(org_id: str, inv_id: str, amount: Decimal) -> None:
    async with session_manager.session() as session:
        invoice = InvoiceDbModel(
            id=inv_id,
            organization_id=org_id,
            invoice_number=f"INV-CONC-{uuid4().hex[:8].upper()}",
            status="ISSUED",
            origin_type="NATIVE",
            source_system="NomadX",
            customer_id=str(uuid4()),
            billing_name="Conc Cust",
            billing_email="conc@example.com",
            billing_address="123 Test St",
            billing_country="US",
            currency_code="USDC",
            currency_type="DIGITAL_ASSET",
            subtotal=str(amount),
            discount_total="0.00",
            charge_total="0.00",
            tax_total="0.00",
            grand_total=str(amount),
            payment_terms_type="DUE_ON_RECEIPT",
            due_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            issued_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
        )
        session.add(invoice)
        await session.commit()

@pytest.mark.asyncio
async def test_concurrent_payment_allocations():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        email = f"owner_fconc_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner FConc",
            "organization_name": "Org FConc Test",
            "organization_slug": f"org-fconc-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]
        
        inv_id = str(uuid4())
        await _seed_invoice(org_id, inv_id, Decimal("1000.00"))

        # Issue event
        event_issued = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="InvoiceIssued",
            payload=json_dumps({
                "invoice_id": inv_id,
                "organization_id": org_id,
                "occurred_on": datetime(2026, 7, 20, tzinfo=timezone.utc).isoformat()
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_issued)
            await session.commit()

        ar_processor = AREventProcessor()
        await ar_processor.process_payment_events()

        # Insert 3 concurrent PaymentReceived events for the same invoice
        async with session_manager.session() as session:
            for _ in range(3):
                ev = OutboxEventDbModel(
                    id=str(uuid4()),
                    event_type="PaymentReceived",
                    payload=json_dumps({
                        "payment_id": str(uuid4()),
                        "invoice_id": inv_id,
                        "amount": "1000.00",
                        "currency": "USDC",
                        "organization_id": org_id,
                        "transaction_reference": f"0xTX-{uuid4().hex[:8]}",
                        "merchant_reference": "MERCH-CONC",
                        "observed_at": "2026-09-03T10:00:00Z"
                    }),
                    processed=False,
                    status="PENDING",
                    created_at=datetime.utcnow()
                )
                session.add(ev)
            await session.commit()

        # Process concurrently
        await asyncio.gather(
            ar_processor.process_payment_events(),
            ar_processor.process_payment_events(),
            ar_processor.process_payment_events()
        )

        # Assert receivable status is either OPEN (all rolled back safely) or SETTLED (one succeeded)
        # And outstanding_amount is either 1000.00 or 0.00, and allocations count matches
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
            assert rec is not None
            assert rec.status.value in ("OPEN", "SETTLED")
            if rec.status.value == "SETTLED":
                assert rec.outstanding_amount.amount == Decimal("0.00")
                assert len(rec.allocations) == 1
            else:
                assert rec.outstanding_amount.amount == Decimal("1000.00")
                assert len(rec.allocations) == 0

def json_dumps(data: dict) -> str:
    import json
    return json.dumps(data)
