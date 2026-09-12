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
    InvoiceDbModel,
    ReceivableDbModel,
    OutboxEventDbModel,
    MembershipDbModel,
    FinancialConsistencyViolationDbModel
)


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
            billing_name="Automation Customer",
            billing_email="customer-auto@example.com",
            billing_address="123 Automation St",
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
            issued_at=datetime.utcnow() - timedelta(minutes=10),
            created_at=datetime.utcnow() - timedelta(minutes=10)
        )
        session.add(invoice_model)
        await session.commit()


async def _seed_receivable_db(org_id: str, rec_id: str, inv_id: str, original_amount: Decimal, due_date: datetime) -> None:
    async with session_manager.session() as session:
        rec = ReceivableDbModel(
            id=rec_id,
            organization_id=org_id,
            customer_id=str(uuid4()),
            invoice_id=inv_id,
            reference=f"AR-{uuid4().hex[:6].upper()}",
            customer_reference="CUST-AUTO-1",
            invoice_reference="INV-AUTO-1",
            currency="USDC",
            original_amount=str(original_amount),
            allocated_amount="0.00",
            outstanding_amount=str(original_amount),
            due_date=due_date,
            status="OPEN",
            aging_bucket="CURRENT",
            dunning_stage="INITIAL",
            idempotency_key=f"idem-{uuid4().hex[:8]}",
            paid_amount="0.00",
            payment_status="UNPAID",
            collection_status="CURRENT",
            created_at=datetime.utcnow() - timedelta(minutes=5),
            updated_at=datetime.utcnow() - timedelta(minutes=5)
        )
        session.add(rec)
        await session.commit()


@pytest.mark.asyncio
async def test_financial_integrity_automation_sweep_and_rbac():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Register Owner (starts as OWNER)
        email_owner = f"owner_auto_{uuid4().hex[:6]}@example.com"
        reg_owner = await ac.post("/auth/register", json={
            "email": email_owner,
            "password": "strongpassword123",
            "name": "Owner Auto",
            "organization_name": "Org Automation",
            "organization_slug": f"org-auto-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_owner = (await ac.post("/auth/login", json={
            "email": email_owner,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg_owner.json()["organization"]["id"]
        headers_owner = {"Authorization": f"Bearer {token_owner}", "X-Organization-ID": org_id}

        # --- SEED INCONSISTENT STATE & STUCK PROCESSING ---
        inv_id = str(uuid4())
        rec_id = str(uuid4())
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        
        await _seed_invoice_db(org_id, inv_id, Decimal("1000.00"), due_date)
        await _seed_receivable_db(org_id, rec_id, inv_id, Decimal("900.00"), due_date)

        stuck_event_id = str(uuid4())
        stuck_event = OutboxEventDbModel(
            id=stuck_event_id,
            event_type="InvoiceIssued",
            payload=json.dumps({
                "invoice_id": str(uuid4()),
                "organization_id": org_id,
                "occurred_on": datetime.utcnow().isoformat()
            }),
            processed=False,
            status="PROCESSING", # stuck!
            attempts=1,
            created_at=datetime.utcnow() - timedelta(minutes=20)
        )
        async with session_manager.session() as session:
            session.add(stuck_event)
            await session.commit()

        # Promote user to PLATFORM_ADMIN in DB to satisfy production gate
        async with session_manager.session() as db_session:
            user_id = reg_owner.json()["user"]["id"]
            stmt = (
                update(MembershipDbModel)
                .where(MembershipDbModel.user_id == user_id)
                .values(role="PLATFORM_ADMIN")
            )
            await db_session.execute(stmt)
            await db_session.commit()

        # --- TRIGGER AUTOMATION SWEEP AS PLATFORM_ADMIN -> SUCCESS ---
        res_sweep = await ac.post("/api/financial-consistency/automation/run", headers=headers_owner)
        assert res_sweep.status_code == 200
        sweep_data = res_sweep.json()
        assert sweep_data["status"] == "COMPLETED"
        assert sweep_data["organizations_swept"] >= 1
        assert sweep_data["total_active_violations"] >= 1
        assert sweep_data["events_recovered"] >= 1

        # Verify stuck event recovered
        async with session_manager.session() as session:
            res_ev = await session.execute(select(OutboxEventDbModel).where(OutboxEventDbModel.id == stuck_event_id))
            ev = res_ev.scalar_one_or_none()
            assert ev.status == "RETRYING"

        # Verify consistency metrics updated
        res_metrics = await ac.get("/api/financial-consistency/metrics", headers=headers_owner)
        assert res_metrics.status_code == 200
        metrics = res_metrics.json()
        assert metrics["status"] == "DEGRADED"
        assert metrics["active_violations"] == 1

        # --- TEST RBAC BOUNDARY (MEMBER ROLE) ---
        # Modify the user's role in MembershipDbModel to "MEMBER"
        async with session_manager.session() as session:
            stmt_upd = (
                update(MembershipDbModel)
                .where(MembershipDbModel.organization_id == org_id)
                .values(role="MEMBER")
            )
            await session.execute(stmt_upd)
            await session.commit()

        # Trigger Automation Sweep as MEMBER -> should fail with HTTP 403 Forbidden
        res_sweep_member = await ac.post("/api/financial-consistency/automation/run", headers=headers_owner)
        assert res_sweep_member.status_code == 403
        assert "platform administrator" in res_sweep_member.json()["detail"].lower()
