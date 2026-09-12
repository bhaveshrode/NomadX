import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_rbac_privilege_escalation_blocks():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. OWNER registers organization
        owner_email = "owner_rbac@example.com"
        reg_res = await ac.post("/auth/register", json={
            "email": owner_email,
            "password": "strongpassword123",
            "name": "David Owner",
            "organization_name": "David Corp",
            "organization_slug": "david-corp-slug",
            "default_currency": "USD"
        })
        assert reg_res.status_code == 201
        org_id = reg_res.json()["organization"]["id"]

        # Owner login
        login_res = await ac.post("/auth/login", json={"email": owner_email, "password": "strongpassword123"})
        owner_token = login_res.json()["token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}", "X-Organization-ID": org_id}

        # 2. OWNER invites MEMBER
        member_email = "member_rbac@example.com"
        await ac.post("/api/organization/members/invite", json={
            "email": member_email,
            "name": "Emma Member",
            "role": "MEMBER"
        }, headers=owner_headers)

        # 3. MEMBER logins
        mem_login_res = await ac.post("/auth/login", json={"email": member_email, "password": "Welcome123!"})
        mem_token = mem_login_res.json()["token"]
        mem_headers = {"Authorization": f"Bearer {mem_token}", "X-Organization-ID": org_id}

        # 4. MEMBER attempts to invite a user -> should yield 403 Forbidden
        bad_invite_res = await ac.post("/api/organization/members/invite", json={
            "email": "hacker@hax.com",
            "name": "Hack User",
            "role": "ADMIN"
        }, headers=mem_headers)
        assert bad_invite_res.status_code == 403

        # 5. MEMBER attempts to upgrade their own role -> should yield 403 Forbidden
        bad_upgrade_res = await ac.put(f"/api/organization/members/{mem_login_res.json()['user']['id']}/role", json={
            "role": "OWNER"
        }, headers=mem_headers)
        assert bad_upgrade_res.status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_isolation_enforcement():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register Org A
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_a@example.com",
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A",
            "organization_slug": "org-a-slug",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        org_a_id = reg_a.json()["organization"]["id"]
        
        # Login A
        login_a = await ac.post("/auth/login", json={"email": "owner_a@example.com", "password": "strongpassword123"})
        token_a = login_a.json()["token"]

        # 2. Register Org B
        reg_b = await ac.post("/auth/register", json={
            "email": "owner_b@example.com",
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B",
            "organization_slug": "org-b-slug",
            "default_currency": "USD"
        })
        assert reg_b.status_code == 201
        org_b_id = reg_b.json()["organization"]["id"]

        # 3. Owner A attempts to access Org B context -> should yield 403 Forbidden
        headers_cross = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_b_id}
        
        cross_members_res = await ac.get("/api/organization/members", headers=headers_cross)
        assert cross_members_res.status_code == 403
        assert "Access denied" in cross_members_res.json()["detail"]

        cross_approvals_res = await ac.get("/api/approvals", headers=headers_cross)
        assert cross_approvals_res.status_code == 403

        cross_billing_res = await ac.get("/api/billing/subscription", headers=headers_cross)
        assert cross_billing_res.status_code == 403


@pytest.mark.asyncio
async def test_approval_manipulation_protection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. OWNER registers organization
        owner_email = "owner_manip@example.com"
        reg_res = await ac.post("/auth/register", json={
            "email": owner_email,
            "password": "strongpassword123",
            "name": "Frank Owner",
            "organization_name": "Frank Corp",
            "organization_slug": "frank-corp-slug",
            "default_currency": "USD"
        })
        org_id = reg_res.json()["organization"]["id"]

        # Owner login
        login_res = await ac.post("/auth/login", json={"email": owner_email, "password": "strongpassword123"})
        owner_token = login_res.json()["token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}", "X-Organization-ID": org_id}

        # 2. OWNER invites MEMBER
        member_email = "member_manip@example.com"
        await ac.post("/api/organization/members/invite", json={
            "email": member_email,
            "name": "Grace Member",
            "role": "MEMBER"
        }, headers=owner_headers)

        # 3. MEMBER logins
        mem_login = await ac.post("/auth/login", json={"email": member_email, "password": "Welcome123!"})
        mem_token = mem_login.json()["token"]
        mem_headers = {"Authorization": f"Bearer {mem_token}", "X-Organization-ID": org_id}

        # 4. Get active approvals (first Owner calls to seed and fetch)
        approvals_res = await ac.get("/api/approvals", headers=owner_headers)
        assert approvals_res.status_code == 200
        target_approval_id = approvals_res.json()[0]["id"]

        # 5. MEMBER attempts to approve -> should yield 403 Forbidden
        bad_decide_res = await ac.post(f"/api/approvals/{target_approval_id}/decide", json={
            "decision": "APPROVED"
        }, headers=mem_headers)
        assert bad_decide_res.status_code == 403
