import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_complete_auth_and_context_api_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register Merchant
        register_payload = {
            "email": "api_test@example.com",
            "password": "strongpassword123",
            "name": "Jane Onboard",
            "organization_name": "Jane Commerce",
            "organization_slug": "jane-commerce-slug",
            "default_currency": "EUR"
        }
        
        reg_response = await ac.post("/auth/register", json=register_payload)
        assert reg_response.status_code == 201
        
        reg_data = reg_response.json()
        assert reg_data["user"]["email"] == "api_test@example.com"
        assert reg_data["user"]["status"] == "PENDING"
        assert reg_data["user"]["email_verified"] is False
        assert "verification_token" in reg_data["user"]
        
        user_id = reg_data["user"]["id"]
        token_val = reg_data["user"]["verification_token"]
        org_id = reg_data["organization"]["id"]

        # Try to register duplicate email -> Should yield 409 Conflict
        dup_response = await ac.post("/auth/register", json=register_payload)
        assert dup_response.status_code == 409

        # Try to login before verifying email -> Status is PENDING but login is allowed (or we verify first)
        # Wait, the user status is PENDING, so login is allowed (we test user.authenticate which resets attempt count and lets pending user login). Let's test that login works.
        login_payload = {
            "email": "api_test@example.com",
            "password": "strongpassword123"
        }
        login_res = await ac.post("/auth/login", json=login_payload)
        assert login_res.status_code == 200
        
        login_data = login_res.json()
        assert "token" in login_data
        jwt_token = login_data["token"]

        # 2. Verify Email
        verify_payload = {
            "user_id": user_id,
            "token": token_val
        }
        verify_res = await ac.post("/auth/verify-email", json=verify_payload)
        assert verify_res.status_code == 200
        assert verify_res.json()["status"] == "success"

        # 3. Test Secure Context Endpoint
        # Attempt without credentials -> 401 Unauthorized
        secure_no_auth = await ac.get("/api/secure-context")
        assert secure_no_auth.status_code == 401

        # Attempt with valid credentials -> 200 OK
        headers = {"Authorization": f"Bearer {jwt_token}"}
        secure_valid = await ac.get("/api/secure-context", headers=headers)
        assert secure_valid.status_code == 200
        
        context_data = secure_valid.json()
        assert context_data["user_id"] == user_id
        assert context_data["organization_id"] == org_id
        assert context_data["role"] == "OWNER"

        # Attempt with valid credentials but specifying an explicit organization ID where user is a member
        headers_explicit = {
            "Authorization": f"Bearer {jwt_token}",
            "X-Organization-ID": org_id
        }
        secure_explicit = await ac.get("/api/secure-context", headers=headers_explicit)
        assert secure_explicit.status_code == 200
        assert secure_explicit.json()["organization_id"] == org_id

        # Attempt with valid credentials but specifying a fake organization ID -> 403 Forbidden (Isolation guard)
        headers_fake_org = {
            "Authorization": f"Bearer {jwt_token}",
            "X-Organization-ID": "fake-org-uuid-999"
        }
        secure_fake_org = await ac.get("/api/secure-context", headers=headers_fake_org)
        assert secure_fake_org.status_code == 403
        assert "Access denied" in secure_fake_org.json()["detail"]


@pytest.mark.asyncio
async def test_auth_api_failed_login():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # Login with non-existent user -> 401
        login_payload_fake = {
            "email": "nonexistent_test@example.com",
            "password": "wrongpassword"
        }
        login_res_fake = await ac.post("/auth/login", json=login_payload_fake)
        assert login_res_fake.status_code == 401
        assert "Invalid credentials" in login_res_fake.json()["detail"]
