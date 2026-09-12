import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

async def _register_and_login(ac: AsyncClient, email: str, slug: str):
    reg_res = await ac.post("/auth/register", json={
        "email": email,
        "password": "Password123!",
        "name": "Support Admin",
        "organization_name": "Support Corp",
        "organization_slug": slug,
        "default_currency": "USD"
    })
    login_res = await ac.post("/auth/login", json={"email": email, "password": "Password123!"})
    token = login_res.json()["token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_support_and_operations_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _register_and_login(ac, "sup_admin_v2@example.com", "sup-corp-v2")

        # 1. Get SLA definitions
        sla_res = await ac.get("/api/support/slas")
        assert sla_res.status_code == 200
        assert len(sla_res.json()["slas"]) == 4

        # 2. Get Public System Status
        status_res = await ac.get("/api/status")
        assert status_res.status_code == 200
        assert status_res.json()["system_status"] in ["ALL_SYSTEMS_OPERATIONAL", "DEGRADED_PERFORMANCE"]

        # 3. Create Support Ticket
        ticket_payload = {
            "title": "Webhook delivery latency spiked to 450ms",
            "description": "Observed elevated response latency during peak settlement runs.",
            "severity": "HIGH"
        }
        create_res = await ac.post("/api/support/tickets", json=ticket_payload, headers=headers)
        assert create_res.status_code == 201
        t_data = create_res.json()
        assert t_data["severity"] == "HIGH"
        assert t_data["status"] == "OPEN"
        assert "target_resolution_by" in t_data
        ticket_id = t_data["id"]

        # 4. List Support Tickets
        list_res = await ac.get("/api/support/tickets", headers=headers)
        assert list_res.status_code == 200
        assert any(t["id"] == ticket_id for t in list_res.json())

        # 5. Resolve Ticket
        resolve_res = await ac.post(f"/api/support/tickets/{ticket_id}/resolve", headers=headers)
        assert resolve_res.status_code == 200
        assert resolve_res.json()["status"] == "RESOLVED"

        # 6. Request Account Recovery
        rec_res = await ac.post("/api/support/recovery/request", json={
            "email": "lostadmin@example.com",
            "justification": "MFA device lost during hardware transition."
        })
        assert rec_res.status_code == 200
        assert rec_res.json()["status"] == "success"
