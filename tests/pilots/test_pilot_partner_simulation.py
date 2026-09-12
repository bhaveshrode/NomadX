import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

async def _register_and_login(ac: AsyncClient, email: str, slug: str):
    reg_res = await ac.post("/auth/register", json={
        "email": email,
        "password": "Password123!",
        "name": f"Pilot Admin {slug}",
        "organization_name": f"Pilot Org {slug}",
        "organization_slug": slug,
        "default_currency": "USD"
    })
    login_res = await ac.post("/auth/login", json={"email": email, "password": "Password123!"})
    token = login_res.json()["token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_pilot_persona_1_merchant_alpha():
    """
    Pilot 1: E-Commerce High-Volume Invoicing & Auto-Dunning Simulation
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _register_and_login(ac, "admin_v2@merchantalpha.com", "merchant-alpha-v2")

        # Create Invoice
        inv_res = await ac.post("/api/invoices", json={
            "customer_id": "00000000-0000-4000-8000-000000009988",
            "currency_code": "USD",
            "payment_terms_type": "NET_30"
        }, headers=headers)
        assert inv_res.status_code == 201



        # Check Subscription Tier & Metering
        sub_res = await ac.get("/api/billing/subscription", headers=headers)
        assert sub_res.status_code == 200


@pytest.mark.asyncio
async def test_pilot_persona_2_global_pay_saas():
    """
    Pilot 2: Cross-Border Settlement & Billing Reconciliation Simulation
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _register_and_login(ac, "admin_v2@globalpay.com", "global-pay-v2")

        # Subscription Upgrade & Reconciliation Check
        upg_res = await ac.post("/api/billing/subscription/upgrade", json={"tier": "ENTERPRISE"}, headers=headers)
        assert upg_res.status_code == 200

        rec_res = await ac.get("/api/billing/reconcile", headers=headers)
        assert rec_res.status_code == 200
        assert rec_res.json()["reconciliation_status"] in ["BALANCED", "ACTION_REQUIRED"]


@pytest.mark.asyncio
async def test_pilot_persona_3_protocol_treasury():
    """
    Pilot 3: Decentralized Treasury - Escalations & Human Approvals Simulation
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _register_and_login(ac, "admin_v2@treasury.com", "treasury-v2")

        # Operational Escalation
        esc_res = await ac.post("/api/notifications/escalations", json={
            "anomaly_title": "Threshold Breach: Treasury Reserve Low",
            "severity": "CRITICAL",
            "details": "Treasury balance fell below minimum operational reserve limit.",
            "channels": ["EMAIL", "IN_APP"]
        }, headers=headers)
        assert esc_res.status_code == 201
        assert esc_res.json()["status"] == "success"

        # Check Status Page
        status_res = await ac.get("/api/status")
        assert status_res.status_code == 200
