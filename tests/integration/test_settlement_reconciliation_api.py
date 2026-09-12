import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import MembershipDbModel, SettlementReconciliationDbModel, OutboxEventDbModel


@pytest.mark.asyncio
async def test_settlement_reconciliation_api_full_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & Login Merchant A (OWNER)
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_a_rec@example.com",
            "password": "strongpassword123",
            "name": "Owner A Rec",
            "organization_name": "Org A Rec",
            "organization_slug": f"org-a-rec-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        assert reg_a.status_code == 201
        token_a = (await ac.post("/auth/login", json={"email": "owner_a_rec@example.com", "password": "strongpassword123"})).json()["token"]
        
        ctx_a = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})).json()
        org_a_id = ctx_a["organization_id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # 2. Register & Login Merchant B (downgraded to MEMBER role)
        reg_b = await ac.post("/auth/register", json={
            "email": "member_b_rec@example.com",
            "password": "strongpassword123",
            "name": "Member B Rec",
            "organization_name": "Org B Rec",
            "organization_slug": f"org-b-rec-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        assert reg_b.status_code == 201
        user_b_id = reg_b.json()["user"]["id"]
        token_b = (await ac.post("/auth/login", json={"email": "member_b_rec@example.com", "password": "strongpassword123"})).json()["token"]
        
        # Downgrade B to MEMBER in database
        async with session_manager.session() as session:
            await session.execute(
                update(MembershipDbModel)
                .where(MembershipDbModel.user_id == user_b_id)
                .values(role="MEMBER")
            )
            await session.commit()

        ctx_b = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_b}"})).json()
        org_b_id = ctx_b["organization_id"]
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Organization-ID": org_b_id
        }

        # --- TEST CASES ---

        # Case 1: Create Settlement (OWNER)
        ref_a = f"SET-REC-{uuid4().hex[:6].upper()}"
        idem_key = f"idem-api-{uuid4().hex[:8]}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref_a,
            "merchant_reference": "MERCH-ORG-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": idem_key
        }, headers=headers_a)
        assert res_create.status_code == 201
        settlement_id = res_create.json()["id"]
        assert res_create.json()["status"] == "DRAFT"
        assert res_create.json()["reference"] == ref_a

        # Case 2: Idempotent Creation (Same key -> returns 200 OK and same data)
        res_idem = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref_a,
            "merchant_reference": "MERCH-ORG-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": idem_key
        }, headers=headers_a)
        assert res_idem.status_code == 200
        assert res_idem.json()["id"] == settlement_id

        # Case 3: Duplicate Reference constraint (Separate key, same ref -> 409)
        res_dup_ref = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref_a,
            "merchant_reference": "MERCH-ORG-A",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"different-key-{uuid4().hex[:6]}"
        }, headers=headers_a)
        assert res_dup_ref.status_code == 409

        # Case 4: RBAC Guard (MEMBER B attempts mutating create -> 403)
        res_memb_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": f"SET-{uuid4().hex[:6].upper()}",
            "merchant_reference": "MERCH-ORG-B",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:6]}"
        }, headers=headers_b)
        assert res_memb_create.status_code == 403

        # Case 5: Open Settlement
        res_open = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers_a)
        assert res_open.status_code == 200
        assert res_open.json()["status"] == "OPEN"

        # Case 6: Add Settlement Entry
        res_entry = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-ENT-1",
            "source_transaction_reference": "TX-101",
            "source_context_type": "INVOICE",
            "gross_amount": 100.00,
            "fees_amount": 5.00,
            "adjustments_amount": 0.00,
            "net_amount": 95.00,
            "currency_code": "USDC"
        }, headers=headers_a)
        assert res_entry.status_code == 200
        assert res_entry.json()["gross_amount"] == "100.00"
        assert res_entry.json()["net_amount"] == "95.00"

        # Attempt currency mismatch
        res_mismatch = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-ENT-2",
            "source_transaction_reference": "TX-102",
            "source_context_type": "INVOICE",
            "gross_amount": 100.00,
            "fees_amount": 5.00,
            "adjustments_amount": 0.00,
            "net_amount": 95.00,
            "currency_code": "EUR"
        }, headers=headers_a)
        assert res_mismatch.status_code == 400

        # Attempt duplicate entry transaction
        res_dup_entry = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-ENT-3",
            "source_transaction_reference": "TX-101", # Duplicate
            "source_context_type": "INVOICE",
            "gross_amount": 100.00,
            "fees_amount": 5.00,
            "adjustments_amount": 0.00,
            "net_amount": 95.00,
            "currency_code": "USDC"
        }, headers=headers_a)
        assert res_dup_entry.status_code == 409

        # Case 7: Add Settlement Adjustment
        res_adj = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/adjustments", json={
            "adjustment_id": "adj-monthly-fee",
            "type": "PROCESSING_FEE",
            "description": "Adjustment charge",
            "amount": 10.00,
            "currency_code": "USDC"
        }, headers=headers_a)
        assert res_adj.status_code == 200
        assert res_adj.json()["adjustments"] == "10.00"
        assert res_adj.json()["net_amount"] == "85.00" # 95 - 10 = 85.00

        # Case 8: Start Reconciliation
        res_rec_start = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/reconciliation/start", headers=headers_a)
        assert res_rec_start.status_code == 200
        assert res_rec_start.json()["status"] == "RECONCILING"

        # Case 9: Record Discrepancy manually
        res_disc = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/discrepancies", json={
            "entry_reference": "LINE-ENT-1",
            "discrepancy_type": "AMOUNT_MISMATCH",
            "expected_amount": 95.00,
            "actual_amount": 85.00,
            "description": "Variance of 10 USDC"
        }, headers=headers_a)
        assert res_disc.status_code == 200
        assert res_disc.json()["status"] == "DISCREPANCY"

        # Try to complete (Should fail: 409 discrepancy exists)
        res_comp_fail = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/complete", headers=headers_a)
        assert res_comp_fail.status_code == 409
        assert "discrepancy_exists" in res_comp_fail.json()["detail"].lower() or "discrepancies" in res_comp_fail.json()["detail"].lower()

        # Case 10: Resolve Discrepancy
        res_resolve = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/discrepancies/LINE-ENT-1/resolve", headers=headers_a)
        assert res_resolve.status_code == 200
        assert res_resolve.json()["status"] == "RECONCILED"

        # Case 11: Complete Settlement (Should succeed now since discrepancies resolved)
        res_comp = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/complete", headers=headers_a)
        assert res_comp.status_code == 200
        assert res_comp.json()["status"] == "COMPLETED"

        # Try to mutate completed settlement (Should fail: 409 finalized)
        res_mutate_fail = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/adjustments", json={
            "adjustment_id": "adj-late",
            "type": "MANUAL_ADJUSTMENT",
            "description": "Late charge",
            "amount": 5.00,
            "currency_code": "USDC"
        }, headers=headers_a)
        assert res_mutate_fail.status_code == 409

        # Case 12: List Settlements with Filtering & Pagination
        res_list = await ac.get(f"/api/settlement-reconciliations?reference={ref_a}&limit=10&offset=0", headers=headers_a)
        assert res_list.status_code == 200
        assert len(res_list.json()) == 1
        assert res_list.json()[0]["id"] == settlement_id

        # Case 13: Tenant Isolation Boundary Checks (Org B attempts reading Org A's settlement -> 404 hidden)
        res_cross_get = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers_b)
        assert res_cross_get.status_code == 404

        res_cross_entries = await ac.get(f"/api/settlement-reconciliations/{settlement_id}/entries", headers=headers_b)
        assert res_cross_entries.status_code == 404

        res_cross_adjs = await ac.get(f"/api/settlement-reconciliations/{settlement_id}/adjustments", headers=headers_b)
        assert res_cross_adjs.status_code == 404

        res_cross_discs = await ac.get(f"/api/settlement-reconciliations/{settlement_id}/discrepancies", headers=headers_b)
        assert res_cross_discs.status_code == 404

        # MEMBER B can read own settlements (empty list)
        res_memb_list = await ac.get("/api/settlement-reconciliations", headers=headers_b)
        assert res_memb_list.status_code == 200
        assert len(res_memb_list.json()) == 0
