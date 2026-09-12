import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

async def _register_and_login(ac: AsyncClient, email: str, slug: str):
    reg_res = await ac.post("/auth/register", json={
        "email": email,
        "password": "Password123!",
        "name": "Escalation Admin",
        "organization_name": "Escalation Corp",
        "organization_slug": slug,
        "default_currency": "USD"
    })
    login_res = await ac.post("/auth/login", json={"email": email, "password": "Password123!"})
    token = login_res.json()["token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_notifications_and_operational_escalation_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _register_and_login(ac, "esc_admin_v2@example.com", "esc-corp-v2")

        # 1. Trigger Critical Anomaly Escalation
        escalation_payload = {
            "anomaly_title": "Unexpected Liquidity Imbalance on Settlement Chain",
            "severity": "CRITICAL",
            "details": "Contract wallet balance dropped below 5.0 ETH safety threshold.",
            "channels": ["EMAIL", "IN_APP"]
        }
        esc_res = await ac.post("/api/notifications/escalations", json=escalation_payload, headers=headers)
        assert esc_res.status_code == 201
        esc_data = esc_res.json()
        assert esc_data["status"] == "success"
        assert esc_data["severity"] == "CRITICAL"
        assert "human_operator_notified" in esc_data
