import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json
from sqlalchemy import select, update

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    SettlementReconciliationDbModel,
    SettlementEntryDbModel,
    SettlementDbModel,
    PaymentDbModel,
    PaymentAllocationDbModel,
    ReceivableDbModel,
    ReceivableAllocationDbModel,
    InvoiceDbModel,
    OutboxEventDbModel
)
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.invoice import InvoiceId


async def _seed_invoice_db(org_id: str, inv_id: str, grand_total: Decimal, due_date: datetime, status: str = "ISSUED") -> None:
    async with session_manager.session() as session:
        cust_id = str(uuid4())
        invoice_model = InvoiceDbModel(
            id=inv_id,
            organization_id=org_id,
            invoice_number=f"INV-{str(uuid4())[:8]}",
            status=status,
            origin_type="NATIVE",
            source_system="NomadX",
            external_invoice_id=None,
            customer_id=cust_id,
            billing_name="Consistency Customer",
            billing_email="customer-consistency@example.com",
            billing_address="123 Consistency St",
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


async def _seed_payment_db(org_id: str, pay_id: str, tx_ref: str, amount: Decimal, reconciliation_status: str = "UNRECONCILED") -> None:
    async with session_manager.session() as session:
        payment = PaymentDbModel(
            id=pay_id,
            organization_id=org_id,
            observed_amount=str(amount),
            asset_code="USDC",
            asset_type="DIGITAL_ASSET",
            asset_decimals=6,
            network="BASE",
            transaction_reference=tx_ref,
            sender="0xSenderWalletConsistency",
            receiver="0xReceiverWalletConsistency",
            status="CONFIRMED",
            reconciliation_status=reconciliation_status,
            observed_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(payment)
        await session.commit()


@pytest.mark.asyncio
async def test_financial_consistency_healthy_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register & login
        email = f"owner_healthy_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Healthy",
            "organization_name": "Org Healthy",
            "organization_slug": f"org-healthy-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed Invoice
        inv_id = str(uuid4())
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        await _seed_invoice_db(org_id, inv_id, Decimal("1000.00"), due_date, status="ISSUED")

        # Simulate InvoiceIssued outbox event
        event_issued = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="InvoiceIssued",
            payload=json.dumps({
                "invoice_id": inv_id,
                "organization_id": org_id,
                "occurred_on": datetime.now(timezone.utc).isoformat()
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_issued)
            await session.commit()

        # Run AR Event Processor to create Receivable
        ar_processor = AREventProcessor()
        await ar_processor.process_payment_events()

        # Check Consistency via API
        res_check = await ac.get("/api/financial-consistency/check", headers=headers)
        assert res_check.status_code == 200
        report = res_check.json()
        assert report["status"] == "HEALTHY"
        assert report["summary"]["total_violations"] == 0

        # Check Metrics
        res_metrics = await ac.get("/api/financial-consistency/metrics", headers=headers)
        assert res_metrics.status_code == 200
        metrics = res_metrics.json()
        assert metrics["status"] == "HEALTHY"
        assert metrics["active_violations"] == 0
        assert float(metrics["health_score"]) == 100.0


@pytest.mark.asyncio
async def test_invoice_receivable_mismatch_detection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_mismatch_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Mismatch",
            "organization_name": "Org Mismatch",
            "organization_slug": f"org-mismatch-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed Invoice
        inv_id = str(uuid4())
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        await _seed_invoice_db(org_id, inv_id, Decimal("1000.00"), due_date, status="ISSUED")

        # 1. Missing Receivable Check
        res_check = await ac.get("/api/financial-consistency/check", headers=headers)
        assert res_check.status_code == 200
        report = res_check.json()
        assert report["status"] == "DEGRADED"
        assert report["summary"]["total_violations"] == 1
        assert report["violations"][0]["violation_type"] == "INVOICE_RECEIVABLE_MISMATCH"
        assert "record is missing" in report["violations"][0]["description"]

        # 2. Value Mismatch Check
        # Seed matching Receivable manually in DB, but with different original amount ($900 instead of $1000)
        async with session_manager.session() as session:
            rec = ReceivableDbModel(
                id=str(uuid4()),
                organization_id=org_id,
                customer_id=str(uuid4()),
                invoice_id=inv_id,
                reference=f"AR-{uuid4().hex[:6].upper()}",
                customer_reference="CUST-1",
                invoice_reference="INV-1",
                currency="USDC",
                original_amount="900.00",
                allocated_amount="0.00",
                outstanding_amount="900.00",
                due_date=due_date,
                status="OPEN",
                aging_bucket="CURRENT",
                dunning_stage="INITIAL",
                idempotency_key="idem-key-1",
                paid_amount="0.00",
                payment_status="UNPAID",
                collection_status="CURRENT",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(rec)
            await session.commit()

        res_check2 = await ac.get("/api/financial-consistency/check", headers=headers)
        report2 = res_check2.json()
        assert report2["status"] == "DEGRADED"
        assert report2["summary"]["total_violations"] == 1
        assert report2["violations"][0]["violation_type"] == "INVOICE_RECEIVABLE_MISMATCH"
        assert "does not match" in report2["violations"][0]["description"]


@pytest.mark.asyncio
async def test_payment_allocation_and_receivable_balance_mismatch_detection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_alloc_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Alloc",
            "organization_name": "Org Alloc",
            "organization_slug": f"org-alloc-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed Invoice & Receivable
        inv_id = str(uuid4())
        rec_id = str(uuid4())
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        await _seed_invoice_db(org_id, inv_id, Decimal("1000.00"), due_date, status="ISSUED")
        
        async with session_manager.session() as session:
            rec = ReceivableDbModel(
                id=rec_id,
                organization_id=org_id,
                customer_id=str(uuid4()),
                invoice_id=inv_id,
                reference=f"AR-{uuid4().hex[:6].upper()}",
                customer_reference="CUST-2",
                invoice_reference="INV-2",
                currency="USDC",
                original_amount="1000.00",
                allocated_amount="0.00",
                outstanding_amount="1000.00",
                due_date=due_date,
                status="OPEN",
                aging_bucket="CURRENT",
                dunning_stage="INITIAL",
                idempotency_key="idem-key-2",
                paid_amount="0.00",
                payment_status="UNPAID",
                collection_status="CURRENT",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(rec)
            await session.commit()

        # Seed Payment & Allocations with a mismatch
        pay_id = str(uuid4())
        tx_ref = f"0xTX-{uuid4().hex[:6].upper()}"
        await _seed_payment_db(org_id, pay_id, tx_ref, Decimal("1000.00"), reconciliation_status="RECONCILED")

        async with session_manager.session() as session:
            # Payment allocation of $1000
            pa = PaymentAllocationDbModel(
                id=str(uuid4()),
                payment_id=pay_id,
                invoice_id=inv_id,
                organization_id=org_id,
                allocated_amount="1000.00",
                currency="USDC",
                status="CONFIRMED",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            # Mismatched receivable allocation of only $900
            ra = ReceivableAllocationDbModel(
                id=str(uuid4()),
                receivable_id=rec_id,
                payment_reference=pay_id,
                allocated_amount="900.00",
                currency="USDC",
                allocated_at=datetime.utcnow(),
                organization_id=org_id
            )
            session.add(pa)
            session.add(ra)
            await session.commit()

        # Run Check API
        res_check = await ac.get("/api/financial-consistency/check", headers=headers)
        report = res_check.json()
        assert report["status"] == "DEGRADED"
        types = [v["violation_type"] for v in report["violations"]]
        assert "PAYMENT_ALLOCATION_MISMATCH" in types
        # Also triggers RECEIVABLE_BALANCE_MISMATCH because receivable allocated_amount column is 0.00, but ra sum is 900.00
        assert "RECEIVABLE_BALANCE_MISMATCH" in types


@pytest.mark.asyncio
async def test_settlement_mismatch_and_prevention_control():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_set_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Settlement",
            "organization_name": "Org Settlement Check",
            "organization_slug": f"org-set-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Create Reconciliation Batch
        ref = f"SET-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-TEST",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers)

        # Add Entry
        tx_ref = f"0xTX-{uuid4().hex[:6].upper()}"
        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-CHECK-1",
            "source_transaction_reference": tx_ref,
            "source_context_type": "PAYMENT",
            "gross_amount": 1000.00,
            "fees_amount": 0.00,
            "adjustments_amount": 0.00,
            "net_amount": 1000.00,
            "currency_code": "USDC"
        }, headers=headers)

        # Seed physical Settlement with mismatched amount ($950 instead of $1000)
        async with session_manager.session() as session:
            phys_settlement = SettlementDbModel(
                id=str(uuid4()),
                organization_id=org_id,
                reference=ref,
                source_payment_id="fake-source",
                asset_code="USDC",
                asset_type="DIGITAL_ASSET",
                asset_decimals=6,
                network="BASE",
                destination_type="WALLET",
                destination_identifier="0xReceiver",
                method="BLOCKCHAIN",
                amount="950.00", # mismatched!
                status="PROCESSING",
                idempotency_key="phys-idem",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(phys_settlement)
            await session.commit()

        # Reconcile manually with MATCHED status to bypass initial discrepancy check
        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/reconciliation/reconcile", json={
            "status": "MATCHED",
            "description": "Forced manual reconciliation match",
            "verified_at": "2026-09-03T10:00:00Z",
            "discrepancies": []
        }, headers=headers)

        # Verify check API detects totals mismatch
        res_check = await ac.get("/api/financial-consistency/check", headers=headers)
        report = res_check.json()
        assert report["status"] == "DEGRADED"
        types = [v["violation_type"] for v in report["violations"]]
        assert "SETTLEMENT_TOTALS_MISMATCH" in types

        # Try to complete the reconciliation batch -> should fail due to consistency check prevention control
        res_comp = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/complete", headers=headers)
        assert res_comp.status_code == 409
        assert "consistency violations" in res_comp.json()["detail"]
