import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import MembershipDbModel, PaymentDbModel


async def _setup_confirmed_payment(org_id: str, tx_ref: str, amount: Decimal, observed_at: datetime) -> None:
    async with session_manager.session() as session:
        # Clear existing to prevent duplicate constraint violation
        from sqlalchemy import delete
        await session.execute(
            delete(PaymentDbModel).where(PaymentDbModel.transaction_reference == tx_ref)
        )
        payment = PaymentDbModel(
            id=str(uuid4()),
            organization_id=org_id,
            observed_amount=str(amount),
            asset_code="USDC",
            asset_type="DIGITAL_ASSET",
            asset_decimals=6,
            network="BASE",
            transaction_reference=tx_ref,
            sender="0xSenderWallet",
            receiver="0xReceiverWallet",
            status="CONFIRMED",
            reconciliation_status="UNRECONCILED",
            observed_at=observed_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(payment)
        await session.commit()


@pytest.mark.asyncio
async def test_settlement_reconciliation_automation_e2e():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & Login Merchant A (OWNER)
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_a_auto@example.com",
            "password": "strongpassword123",
            "name": "Owner A Auto",
            "organization_name": "Org A Auto",
            "organization_slug": f"org-a-auto-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        assert reg_a.status_code == 201
        token_a = (await ac.post("/auth/login", json={"email": "owner_a_auto@example.com", "password": "strongpassword123"})).json()["token"]
        
        ctx_a = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})).json()
        org_a_id = ctx_a["organization_id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # 2. Insert corresponding confirmed payment in database
        tx_ref = f"0xTX-{uuid4().hex[:8].upper()}"
        payment_date = datetime(2026, 9, 2, 12, 0)
        await _setup_confirmed_payment(org_a_id, tx_ref, Decimal("500.00"), payment_date)

        # 3. Create Draft Settlement
        ref_a = f"SET-REC-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref_a,
            "merchant_reference": "MERCH-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers_a)
        assert res_create.status_code == 201
        settlement_id = res_create.json()["id"]

        # 4. Open Settlement
        res_open = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers_a)
        assert res_open.status_code == 200

        # 5. Add Entry matching the payment
        res_entry = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-AUTO-1",
            "source_transaction_reference": tx_ref,
            "source_context_type": "PAYMENT",
            "gross_amount": 500.00,
            "fees_amount": 0.00,
            "adjustments_amount": 0.00,
            "net_amount": 500.00,
            "currency_code": "USDC"
        }, headers=headers_a)
        assert res_entry.status_code == 200

        # 6. Trigger automated worker processing via POST /process
        res_proc = await ac.post("/api/settlement-reconciliations/process", headers=headers_a)
        assert res_proc.status_code == 200
        assert res_proc.json()["processed_batches"] >= 1

        # 7. Verify settlement was reconciled and completed successfully
        res_get = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers_a)
        assert res_get.status_code == 200
        assert res_get.json()["status"] == "COMPLETED"
        assert res_get.json()["reconciliation_status"] == "MATCHED"


@pytest.mark.asyncio
async def test_settlement_reconciliation_discrepancy_resolution_e2e():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_disc_auto@example.com",
            "password": "strongpassword123",
            "name": "Owner Disc Auto",
            "organization_name": "Org Disc Auto",
            "organization_slug": f"org-disc-auto-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_disc_auto@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # Setup draft settlement
        ref = f"SET-{uuid4().hex[:6].upper()}"
        c_res = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-DISC",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers_a)
        sid = c_res.json()["id"]

        await ac.post(f"/api/settlement-reconciliations/{sid}/open", headers=headers_a)

        # Add entry where NO corresponding payment will be found (will trigger MISSING_TRANSACTION)
        tx_ref_missing = f"0xTX-{uuid4().hex[:8].upper()}"
        await ac.post(f"/api/settlement-reconciliations/{sid}/entries", json={
            "entry_reference": "LINE-DISC-1",
            "source_transaction_reference": tx_ref_missing,
            "source_context_type": "PAYMENT",
            "gross_amount": 1000.00,
            "fees_amount": 0.00,
            "adjustments_amount": 0.00,
            "net_amount": 1000.00,
            "currency_code": "USDC"
        }, headers=headers_a)

        # Reconcile -> status should go to DISCREPANCY
        res_rec = await ac.post(f"/api/settlement-reconciliations/{sid}/reconcile", headers=headers_a)
        assert res_rec.status_code == 200
        assert res_rec.json()["status"] == "DISCREPANCY"
        assert res_rec.json()["reconciliation_status"] == "DISCREPANCY"

        # Check discrepancies list
        res_discs = await ac.get(f"/api/settlement-reconciliations/{sid}/discrepancies", headers=headers_a)
        assert len(res_discs.json()) == 1
        assert res_discs.json()[0]["discrepancy_type"] == "MISSING_TRANSACTION"

        # Try to complete (must fail 409)
        res_comp_fail = await ac.post(f"/api/settlement-reconciliations/{sid}/complete", headers=headers_a)
        assert res_comp_fail.status_code == 409

        # Now resolve the discrepancy manually
        res_resolve = await ac.post(f"/api/settlement-reconciliations/{sid}/discrepancies/LINE-DISC-1/resolve", headers=headers_a)
        assert res_resolve.status_code == 200
        assert res_resolve.json()["status"] == "RECONCILED"

        # Reconcile again now that discrepancy is resolved -> status should go to RECONCILED/COMPLETED
        res_comp = await ac.post(f"/api/settlement-reconciliations/{sid}/complete", headers=headers_a)
        assert res_comp.status_code == 200
        assert res_comp.json()["status"] == "COMPLETED"
