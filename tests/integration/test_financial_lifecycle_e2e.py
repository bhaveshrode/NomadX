import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json
from sqlalchemy import select

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    SettlementReconciliationDbModel,
    PaymentDbModel,
    OutboxEventDbModel,
    InvoiceDbModel,
    ReceivableDbModel
)
from nomadx.src.bootstrap.container import container
from nomadx.src.domain.settlement_reconciliation.value_objects import SettlementId
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.invoice import InvoiceId
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor
from nomadx.src.infrastructure.workers.settlement_event_processor import SettlementEventProcessor
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository


async def _seed_invoice_db(org_id: str, inv_id: str, grand_total: Decimal, due_date: datetime) -> None:
    async with session_manager.session() as session:
        cust_id = str(uuid4())
        invoice_model = InvoiceDbModel(
            id=inv_id,
            organization_id=org_id,
            invoice_number=f"INV-{str(uuid4())[:8]}",
            status="ISSUED",
            origin_type="NATIVE",
            source_system="NomadX",
            external_invoice_id=None,
            customer_id=cust_id,
            billing_name="E2E Lifecycle Customer",
            billing_email="customer-lifecycle-e2e@example.com",
            billing_address="123 Test St",
            billing_country="US",
            billing_tax_identifier=None,
            currency_code="USDC",
            currency_type="DIGITAL_ASSET",
            subtotal=str(grand_total),
            discount_total="0.00",
            charge_total="0.00",
            tax_total="0.00",
            grand_total=str(grand_total),
            payment_terms_type="DUE_ON_RECEIPT",
            due_at=due_date,
            issued_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)
        )
        session.add(invoice_model)
        await session.commit()


async def _setup_confirmed_payment_db(org_id: str, tx_ref: str, amount: Decimal, observed_at: datetime) -> None:
    async with session_manager.session() as session:
        payment = PaymentDbModel(
            id=str(uuid4()),
            organization_id=org_id,
            observed_amount=str(amount),
            asset_code="USDC",
            asset_type="DIGITAL_ASSET",
            asset_decimals=6,
            network="BASE",
            transaction_reference=tx_ref,
            sender="0xSenderWalletLifecycleE2E",
            receiver="0xReceiverWalletLifecycleE2E",
            status="CONFIRMED",
            reconciliation_status="UNRECONCILED",
            observed_at=observed_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(payment)
        await session.commit()


@pytest.mark.asyncio
async def test_full_financial_lifecycle_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & login Org A (Admin/Owner)
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_lifecycle_a@example.com",
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A Lifecycle",
            "organization_slug": f"org-lifecycle-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_lifecycle_a@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg_a.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_id}

        # 2. Seed Invoice of $1000.00 USDC in DB
        inv_id = str(uuid4())
        due_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
        await _seed_invoice_db(org_id, inv_id, Decimal("1000.00"), due_date)

        # 3. Simulate InvoiceIssued Outbox Event
        event_issued = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="InvoiceIssued",
            payload=json.dumps({
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

        # Run AR Event Processor to consume event and create Receivable
        ar_processor = AREventProcessor()
        processed_issued = await ar_processor.process_payment_events()
        assert processed_issued == 1

        # Assert Receivable is created in database
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
            assert rec is not None
            assert rec.status.value == "OPEN"
            assert rec.outstanding_amount.amount == Decimal("1000.00")

        # 4. Simulate PaymentReceived event in outbox (allocates to receivable first)
        correlation_id = str(uuid4())
        payment_id = str(uuid4())
        tx_ref = f"0xTX-LIFECYCLE-{uuid4().hex[:6].upper()}"
        
        event_payment_ar = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentReceived",
            payload=json.dumps({
                "payment_id": payment_id,
                "invoice_id": inv_id,
                "amount": "1000.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref,
                "merchant_reference": "MERCH-AUTO",
                "observed_at": "2026-09-03T10:00:00Z",
                "_metadata": {
                    "correlation_id": correlation_id,
                    "causation_id": correlation_id
                }
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_payment_ar)
            await session.commit()

        # Run AR Event Processor to allocate payment to Receivable
        processed_payment_ar = await ar_processor.process_payment_events()
        assert processed_payment_ar == 1

        # Verify Receivable is SETTLED
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
            assert rec.status.value == "SETTLED"
            assert rec.outstanding_amount.amount == Decimal("0.00")

        # Now simulate PaymentReceived for Settlement
        event_payment_set = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentReceived",
            payload=json.dumps({
                "payment_id": payment_id,
                "invoice_id": inv_id,
                "amount": "1000.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref,
                "merchant_reference": "MERCH-AUTO",
                "observed_at": "2026-09-03T10:00:00Z",
                "_metadata": {
                    "correlation_id": correlation_id,
                    "causation_id": correlation_id
                }
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_payment_set)
            await session.commit()

        # Run Settlement Event Processor to ingest PaymentReceived event and auto-create batch
        async with container.settlement_event_processor() as set_processor:
            processed_payment_set = await set_processor.process_financial_events()
            assert processed_payment_set == 1

        # Retrieve the auto-created Settlement batch ID
        async with session_manager.session() as session:
            stmt = select(SettlementReconciliationDbModel).where(
                SettlementReconciliationDbModel.organization_id == org_id,
                SettlementReconciliationDbModel.merchant_reference == "MERCH-AUTO"
            )
            res = await session.execute(stmt)
            settlement_db = res.scalar_one()
            settlement_id = settlement_db.id
            assert settlement_db.status == "OPEN"

        # 5. Seed matching Payment in Database
        await _setup_confirmed_payment_db(org_id, tx_ref, Decimal("1000.00"), datetime(2026, 9, 3, 10, 0, 0))

        # 6. Run Settlement Automation Worker to Auto-reconcile and Auto-complete
        async with container.settlement_automation_worker() as set_worker:
            polled = await set_worker.process_eligible_settlements()
            assert polled == 1

        # Verify Settlement batch status becomes COMPLETED
        res_detail = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers)
        assert res_detail.json()["status"] == "COMPLETED"
        assert res_detail.json()["reconciliation_status"] == "MATCHED"

        # 7. Financial Amount Consistency Checks
        dto = res_detail.json()
        assert Decimal(dto["gross_amount"]) == Decimal("1000.00")
        assert Decimal(dto["net_amount"]) == Decimal("1000.00")
        assert Decimal(dto["fees"]) == Decimal("0.00")
        assert Decimal(dto["adjustments"]) == Decimal("0.00")

        # 8. Idempotent event replay check: process payment received again, should ignore duplicate event
        async with session_manager.session() as session:
            event_payment_ar.processed = False
            event_payment_ar.status = "PENDING"
            await session.commit()

        processed_dup = await ar_processor.process_payment_events()
        assert processed_dup == 1

        # Outstanding and allocated amounts should not change
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
            assert rec.status.value == "SETTLED"
            assert rec.outstanding_amount.amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_partial_payment_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_lifecycle_part@example.com",
            "password": "strongpassword123",
            "name": "Owner Part",
            "organization_name": "Org Part Lifecycle",
            "organization_slug": f"org-lifecycle-part-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_lifecycle_part@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg_a.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_id}

        # Seed Invoice of $1000.00 USDC in DB
        inv_id = str(uuid4())
        due_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
        await _seed_invoice_db(org_id, inv_id, Decimal("1000.00"), due_date)

        # Seed Receivable directly to speed up partial payment scenario
        async with session_manager.session() as session:
            # Trigger InvoiceIssued event
            event_issued = OutboxEventDbModel(
                id=str(uuid4()),
                event_type="InvoiceIssued",
                payload=json.dumps({
                    "invoice_id": inv_id,
                    "organization_id": org_id,
                    "occurred_on": datetime(2026, 7, 20, tzinfo=timezone.utc).isoformat()
                }),
                processed=False,
                status="PENDING",
                created_at=datetime.utcnow()
            )
            session.add(event_issued)
            await session.commit()

        ar_processor = AREventProcessor()
        await ar_processor.process_payment_events()

        # Simulate Partial PaymentReceived event in outbox ($400.00 USDC) for AR
        payment_id = str(uuid4())
        tx_ref = f"0xTX-PART-{uuid4().hex[:6].upper()}"
        
        event_payment_ar = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentReceived",
            payload=json.dumps({
                "payment_id": payment_id,
                "invoice_id": inv_id,
                "amount": "400.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref,
                "merchant_reference": "MERCH-AUTO",
                "observed_at": "2026-09-03T11:00:00Z"
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_payment_ar)
            await session.commit()

        # Process payment in AR
        await ar_processor.process_payment_events()

        # Verify Receivable outstanding is $600.00 USDC and status is PARTIALLY_PAID
        async with session_manager.session() as session:
            receivable_repo = PostgresReceivableRepository(session)
            rec = await receivable_repo.find_by_invoice(InvoiceId(inv_id))
            assert rec.status.value == "PARTIALLY_PAID"
            assert rec.outstanding_amount.amount == Decimal("600.00")

        # Now simulate PaymentReceived event in outbox for Settlement
        event_payment_set = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentReceived",
            payload=json.dumps({
                "payment_id": payment_id,
                "invoice_id": inv_id,
                "amount": "400.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref,
                "merchant_reference": "MERCH-AUTO",
                "observed_at": "2026-09-03T11:00:00Z"
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_payment_set)
            await session.commit()

        # Ingest payment in Settlement context
        async with container.settlement_event_processor() as set_processor:
            await set_processor.process_financial_events()

        # Check detail -> Settlement entry gross amount is $400.00 USDC
        async with session_manager.session() as session:
            stmt = select(SettlementReconciliationDbModel).where(
                SettlementReconciliationDbModel.organization_id == org_id,
                SettlementReconciliationDbModel.merchant_reference == "MERCH-AUTO"
            )
            res = await session.execute(stmt)
            settlement_db = res.scalar_one()
            settlement_id = settlement_db.id

        res_detail = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers)
        assert Decimal(res_detail.json()["gross_amount"]) == Decimal("400.00")


@pytest.mark.asyncio
async def test_cross_context_multi_tenant_isolation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # Org A
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_isol_a@example.com",
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A Isol",
            "organization_slug": f"org-isol-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_isol_a@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a_id}

        # Org B
        reg_b = await ac.post("/auth/register", json={
            "email": "owner_isol_b@example.com",
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B Isol",
            "organization_slug": f"org-isol-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={"email": "owner_isol_b@example.com", "password": "strongpassword123"})).json()["token"]
        org_b_id = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b_id}

        # Create Settlement in Org A
        ref = f"SET-ISOL-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers_a)
        settlement_id = res_create.json()["id"]

        # Org B tries to access Org A's Settlement detail -> returns 404
        res_detail_b = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers_b)
        assert res_detail_b.status_code == 404


@pytest.mark.asyncio
async def test_refund_and_reversal_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_lifecycle_refund@example.com",
            "password": "strongpassword123",
            "name": "Owner Refund",
            "organization_name": "Org Refund Lifecycle",
            "organization_slug": f"org-lifecycle-refund-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_lifecycle_refund@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg_a.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_id}

        # Simulate PaymentRefunded outbox event (-$300.00)
        refund_id = str(uuid4())
        tx_ref_ref = f"0xTX-REFUND-{uuid4().hex[:6].upper()}"
        event_refund = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentRefunded",
            payload=json.dumps({
                "payment_id": refund_id,
                "amount": "300.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref_ref,
                "merchant_reference": "MERCH-AUTO",
                "observed_at": "2026-09-03T12:00:00Z"
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_refund)
            await session.commit()

        # Run Settlement Event Processor to ingest Refund event
        async with container.settlement_event_processor() as set_processor:
            processed = await set_processor.process_financial_events()
            assert processed == 1

        # Retrieve the auto-created batch and assert that a negative entry of -$300.00 was created
        async with session_manager.session() as session:
            stmt = select(SettlementReconciliationDbModel).where(
                SettlementReconciliationDbModel.organization_id == org_id,
                SettlementReconciliationDbModel.merchant_reference == "MERCH-AUTO"
            )
            res = await session.execute(stmt)
            settlement_db = res.scalar_one()
            settlement_id = settlement_db.id

        res_detail = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers)
        assert Decimal(res_detail.json()["gross_amount"]) == Decimal("-300.00")

