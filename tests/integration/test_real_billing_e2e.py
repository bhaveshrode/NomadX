import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

async def _register_and_login(ac: AsyncClient, email: str, slug: str):
    reg_res = await ac.post("/auth/register", json={
        "email": email,
        "password": "Password123!",
        "name": "Billing Owner",
        "organization_name": "Billing Corp",
        "organization_slug": slug,
        "default_currency": "USD"
    })
    data = reg_res.json()
    login_res = await ac.post("/auth/login", json={"email": email, "password": "Password123!"})
    token = login_res.json()["token"]
    return {"Authorization": f"Bearer {token}"}, data["organization"]["id"]

@pytest.mark.asyncio
async def test_real_billing_architecture_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers, org_id = await _register_and_login(ac, "billing_tester_v2@example.com", "billing-corp-v2")

        # 1. Get initial subscription (TRIAL)
        sub_res = await ac.get("/api/billing/subscription", headers=headers)
        assert sub_res.status_code == 200
        assert sub_res.json()["tier"] == "TRIAL"

        # 2. Upgrade Subscription to GROWTH
        upg_res = await ac.post("/api/billing/subscription/upgrade", json={"tier": "GROWTH"}, headers=headers)
        assert upg_res.status_code == 200
        assert upg_res.json()["subscription"]["tier"] == "GROWTH"
        assert upg_res.json()["subscription"]["price_per_month"] == 199.0

        # 3. Usage Metering
        meter_res = await ac.get("/api/billing/metering", headers=headers)
        assert meter_res.status_code == 200
        assert "invoices_processed_count" in meter_res.json()

        # 4. List Subscription Invoices
        inv_res = await ac.get("/api/billing/invoices", headers=headers)
        assert inv_res.status_code == 200
        assert len(inv_res.json()) >= 1

        # 5. Billing Reconciliation
        rec_res = await ac.get("/api/billing/reconcile", headers=headers)
        assert rec_res.status_code == 200
        assert rec_res.json()["reconciliation_status"] in ["BALANCED", "ACTION_REQUIRED"]

        # 6. Refund Request
        ref_res = await ac.post("/api/billing/refunds", json={"invoice_id": "SUB-INV-001", "reason": "Accidental double charge"}, headers=headers)
        assert ref_res.status_code == 200
        assert ref_res.json()["status"] == "success"

        # 7. Webhook Idempotency (duplicate headers)
        webhook_payload = {
            "event_id": "evt_test_12345",
            "type": "invoice.payment_succeeded",
            "organization_id": org_id,
            "data": {}
        }
        wb1 = await ac.post("/api/billing/webhooks", json=webhook_payload, headers={"Idempotency-Key": "idemp-001"})
        assert wb1.status_code == 200
        assert wb1.json()["status"] == "success"

        wb2 = await ac.post("/api/billing/webhooks", json=webhook_payload, headers={"Idempotency-Key": "idemp-001"})
        assert wb2.status_code == 200
        assert wb2.json()["status"] == "duplicate"

        # 8. Subscription Cancellation
        cancel_res = await ac.post("/api/billing/subscription/cancel", headers=headers)
        assert cancel_res.status_code == 200
        assert cancel_res.json()["subscription"]["status"] == "CANCELED"
