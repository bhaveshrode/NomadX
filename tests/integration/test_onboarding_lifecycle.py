import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_full_onboarding_and_organization_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Merchant Registration
        reg_payload = {
            "email": "lifecycle_owner_v2@example.com",
            "password": "Password123!",
            "name": "Lifecycle Owner",
            "organization_name": "Lifecycle Corp",
            "organization_slug": "lifecycle-corp-v2",
            "default_currency": "USD"
        }
        res = await ac.post("/auth/register", json=reg_payload)
        assert res.status_code == 201
        data = res.json()
        org_id = data["organization"]["id"]
        user_id = data["user"]["id"]

        # 2. Login to obtain token
        login_res = await ac.post("/auth/login", json={
            "email": "lifecycle_owner_v2@example.com",
            "password": "Password123!"
        })
        assert login_res.status_code == 200
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 3. Get Organization Settings
        settings_res = await ac.get("/api/organization/settings", headers=headers)
        assert settings_res.status_code == 200
        assert settings_res.json()["name"] == "Lifecycle Corp"

        # 4. Update Organization Settings
        update_settings_res = await ac.put("/api/organization/settings", json={
            "webhook_url": "https://hooks.lifecyclecorp.com/events",
            "auto_dunning_enabled": False
        }, headers=headers)
        assert update_settings_res.status_code == 200
        assert update_settings_res.json()["webhook_url"] == "https://hooks.lifecyclecorp.com/events"
        assert update_settings_res.json()["auto_dunning_enabled"] is False

        # 5. Invite Operator
        invite_res = await ac.post("/api/organization/members/invite", json={
            "email": "operator_dev_v2@example.com",
            "name": "Dev Operator",
            "role": "OPERATOR"
        }, headers=headers)
        assert invite_res.status_code == 201

        # 6. List Members
        members_res = await ac.get("/api/organization/members", headers=headers)
        assert members_res.status_code == 200
        members = members_res.json()
        assert len(members) >= 2

        # 7. Account Recovery (Forgot Password & Reset)
        forgot_res = await ac.post("/auth/forgot-password", json={"email": "lifecycle_owner_v2@example.com"})
        assert forgot_res.status_code == 200
        rst_token = forgot_res.json()["reset_token"]

        reset_res = await ac.post("/auth/reset-password", json={
            "token": rst_token,
            "new_password": "NewSecurePassword123!"
        })
        assert reset_res.status_code == 200

        # 8. Organization Deactivation
        deactivate_res = await ac.post("/api/organization/deactivate", headers=headers)
        assert deactivate_res.status_code == 200
        assert deactivate_res.json()["status"] == "success"
