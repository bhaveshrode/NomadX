import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_e2e_user_persona_journey():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. OWNER registers organization
        owner_email = "owner_journey@example.com"
        reg_payload = {
            "email": owner_email,
            "password": "strongpassword123",
            "name": "Alice Owner",
            "organization_name": "Alice Corp",
            "organization_slug": "alice-corp-slug",
            "default_currency": "USD"
        }
        reg_res = await ac.post("/auth/register", json=reg_payload)
        assert reg_res.status_code == 201
        reg_data = reg_res.json()
        owner_id = reg_data["user"]["id"]
        org_id = reg_data["organization"]["id"]

        # Owner login
        login_res = await ac.post("/auth/login", json={"email": owner_email, "password": "strongpassword123"})
        assert login_res.status_code == 200
        owner_token = login_res.json()["token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}", "X-Organization-ID": org_id}

        # 2. OWNER invites ADMIN
        admin_email = "admin_journey@example.com"
        invite_admin_payload = {
            "email": admin_email,
            "name": "Bob Admin",
            "role": "ADMIN"
        }
        invite_admin_res = await ac.post("/api/organization/members/invite", json=invite_admin_payload, headers=owner_headers)
        assert invite_admin_res.status_code == 201

        # 3. ADMIN logins and invites OPERATOR
        admin_login_res = await ac.post("/auth/login", json={"email": admin_email, "password": "Welcome123!"})
        assert admin_login_res.status_code == 200
        admin_token = admin_login_res.json()["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}", "X-Organization-ID": org_id}

        op_email = "operator_journey@example.com"
        invite_op_payload = {
            "email": op_email,
            "name": "Charlie Operator",
            "role": "OPERATOR"
        }
        invite_op_res = await ac.post("/api/organization/members/invite", json=invite_op_payload, headers=admin_headers)
        assert invite_op_res.status_code == 201

        # 4. OPERATOR logins and queries approvals
        op_login_res = await ac.post("/auth/login", json={"email": op_email, "password": "Welcome123!"})
        assert op_login_res.status_code == 200
        op_token = op_login_res.json()["token"]
        op_headers = {"Authorization": f"Bearer {op_token}", "X-Organization-ID": org_id}

        # Query approvals (seeds mock approvals)
        approvals_res = await ac.get("/api/approvals", headers=op_headers)
        assert approvals_res.status_code == 200
        approvals_list = approvals_res.json()
        assert len(approvals_list) >= 2
        
        target_approval_id = approvals_list[0]["id"]
        assert approvals_list[0]["status"] == "PENDING"

        # 5. OPERATOR approves the request
        decide_payload = {
            "decision": "APPROVED",
            "notes": "Verified. RPC connection restored, retry settlement."
        }
        decide_res = await ac.post(f"/api/approvals/{target_approval_id}/decide", json=decide_payload, headers=op_headers)
        assert decide_res.status_code == 200
        assert decide_res.json()["status"] == "success"

        # Verify status updated
        approvals_check_res = await ac.get("/api/approvals", headers=op_headers)
        checked_list = approvals_check_res.json()
        target_req = next(r for r in checked_list if r["id"] == target_approval_id)
        assert target_req["status"] == "APPROVED"
        assert target_req["decided_by"] == op_login_res.json()["user"]["id"]


@pytest.mark.asyncio
async def test_billing_metering_integrity():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register organization
        email = "billing_journey@example.com"
        reg_res = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Bill Owner",
            "organization_name": "Bill Corp",
            "organization_slug": "bill-corp-slug",
            "default_currency": "USD"
        })
        assert reg_res.status_code == 201
        org_id = reg_res.json()["organization"]["id"]

        # Login
        login_res = await ac.post("/auth/login", json={"email": email, "password": "strongpassword123"})
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 2. Check initial metering counts (should be 0)
        met_res = await ac.get("/api/billing/metering", headers=headers)
        assert met_res.status_code == 200
        met_data = met_res.json()
        assert met_data["invoices_processed_count"] == 0
        assert met_data["payments_settled_count"] == 0
        assert met_data["payments_settled_volume"] == 0.0

        # 3. Create mock invoices
        invoice_payload = {
            "invoice_id": "99ea5c1a-2895-46fd-b530-e8deef123abc",
            "origin_type": "NATIVE",
            "currency_code": "USD",
            "currency_type": "FIAT",
            "payment_terms_type": "NET_30"
        }
        create_inv_res = await ac.post("/api/invoices", json=invoice_payload, headers=headers)
        assert create_inv_res.status_code == 201

        # 4. Check updated metering (Invoices processed should be 1)
        met_res_updated = await ac.get("/api/billing/metering", headers=headers)
        assert met_res_updated.status_code == 200
        assert met_res_updated.json()["invoices_processed_count"] == 1
