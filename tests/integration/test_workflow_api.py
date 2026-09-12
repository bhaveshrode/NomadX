import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.models import (
    UserDbModel,
    MembershipDbModel,
    OutboxEventDbModel,
    WorkflowDbModel,
    WorkflowVersionDbModel
)


@pytest.mark.asyncio
async def test_workflow_api_lifecycle_and_rules(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Onboard Owner (User A)
        reg_owner = await ac.post("/auth/register", json={
            "email": "owner@nomadx.com",
            "password": "strongpassword123",
            "name": "Owner User",
            "organization_name": "Org A",
            "organization_slug": "org-a-slug",
            "default_currency": "USD"
        })
        assert reg_owner.status_code == 201

        login_owner = await ac.post("/auth/login", json={
            "email": "owner@nomadx.com",
            "password": "strongpassword123"
        })
        token_owner = login_owner.json()["token"]
        
        ctx_owner = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_owner}"})
        org_id = ctx_owner.json()["organization_id"]
        owner_user_id = ctx_owner.json()["user_id"]

        # 2. Onboard Admin (User B)
        reg_admin = await ac.post("/auth/register", json={
            "email": "admin@nomadx.com",
            "password": "strongpassword123",
            "name": "Admin User",
            "organization_name": "Org B",
            "organization_slug": "org-b-slug",
            "default_currency": "USD"
        })
        assert reg_admin.status_code == 201

        # 3. Onboard Member (User C)
        reg_member = await ac.post("/auth/register", json={
            "email": "member@nomadx.com",
            "password": "strongpassword123",
            "name": "Member User",
            "organization_name": "Org C",
            "organization_slug": "org-c-slug",
            "default_currency": "USD"
        })
        assert reg_member.status_code == 201

        # We will make User B (admin) and User C (member) belong to Org A in the database
        db_session.expire_all()
        
        # Query User IDs
        admin_user = (await db_session.execute(select(UserDbModel).where(UserDbModel.email == "admin@nomadx.com"))).scalar_one()
        member_user = (await db_session.execute(select(UserDbModel).where(UserDbModel.email == "member@nomadx.com"))).scalar_one()

        # Add memberships to Org A
        admin_membership = MembershipDbModel(organization_id=org_id, user_id=admin_user.id, role="ADMIN", status="ACTIVE")
        member_membership = MembershipDbModel(organization_id=org_id, user_id=member_user.id, role="MEMBER", status="ACTIVE")
        db_session.add(admin_membership)
        db_session.add(member_membership)
        await db_session.commit()

        # Log in Admin and Member to get tokens
        login_admin = await ac.post("/auth/login", json={"email": "admin@nomadx.com", "password": "strongpassword123"})
        token_admin = login_admin.json()["token"]

        login_member = await ac.post("/auth/login", json={"email": "member@nomadx.com", "password": "strongpassword123"})
        token_member = login_member.json()["token"]

        # Request Headers
        headers_owner = {"Authorization": f"Bearer {token_owner}", "X-Organization-ID": org_id}
        headers_admin = {"Authorization": f"Bearer {token_admin}", "X-Organization-ID": org_id}
        headers_member = {"Authorization": f"Bearer {token_member}", "X-Organization-ID": org_id}

        # 4. Onboard another Tenant (Org D) to test isolation
        reg_tenant = await ac.post("/auth/register", json={
            "email": "tenant@nomadx.com",
            "password": "strongpassword123",
            "name": "Tenant User",
            "organization_name": "Org D",
            "organization_slug": "org-d-slug",
            "default_currency": "USD"
        })
        assert reg_tenant.status_code == 201
        
        login_tenant = await ac.post("/auth/login", json={"email": "tenant@nomadx.com", "password": "strongpassword123"})
        token_tenant = login_tenant.json()["token"]
        ctx_tenant = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_tenant}"})
        org_tenant_id = ctx_tenant.json()["organization_id"]
        headers_tenant = {"Authorization": f"Bearer {token_tenant}", "X-Organization-ID": org_tenant_id}

        # --- Test Workflow Creation ---
        payload = {
            "reference": "WF-API-TEST-1",
            "name": "API Automated Workflow",
            "description": "API Test",
            "trigger_event_type": "InvoiceIssued",
            "conditions_data": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "invoice.amount",
                        "operator": "GREATER_THAN",
                        "value": 1000
                    }
                ]
            },
            "actions_data": [
                {
                    "action_type": "SEND_NOTIFICATION",
                    "parameters": {"template_id": "remind-template"}
                }
            ]
        }

        # A. Member attempts to create workflow -> Expect 403 Forbidden
        res_mem_create = await ac.post("/api/workflows", json=payload, headers=headers_member)
        assert res_mem_create.status_code == 403

        # B. Admin creates workflow -> Expect 201 Created (since it uses header/body Idempotency-Key)
        # We specify Idempotency-Key header
        headers_admin_idem = {**headers_admin, "Idempotency-Key": "idem-key-api-1"}
        res_admin_create = await ac.post("/api/workflows", json=payload, headers=headers_admin_idem)
        assert res_admin_create.status_code == 201
        wf_id = res_admin_create.json()["id"]

        # C. Repeat creation with same idempotency key -> Expect 200 OK
        res_repeat = await ac.post("/api/workflows", json=payload, headers=headers_admin_idem)
        assert res_repeat.status_code == 200
        assert res_repeat.json()["id"] == wf_id

        # D. Duplicate reference rejection
        payload_dup_ref = {**payload, "idempotency_key": "idem-key-api-2"}
        headers_admin_dup = {**headers_admin, "Idempotency-Key": "idem-key-api-2"}
        res_dup_ref = await ac.post("/api/workflows", json=payload_dup_ref, headers=headers_admin_dup)
        assert res_dup_ref.status_code == 409

        # --- Test Workflow Details ---
        # A. Member can view details -> Expect 200 OK
        res_details = await ac.get(f"/api/workflows/{wf_id}", headers=headers_member)
        assert res_details.status_code == 200
        assert res_details.json()["name"] == "API Automated Workflow"

        # B. Isolated Tenant attempts to view details -> Expect 404 Not Found (no leaks)
        res_tenant_get = await ac.get(f"/api/workflows/{wf_id}", headers=headers_tenant)
        assert res_tenant_get.status_code == 404

        # --- Test Workflow Listing ---
        res_list = await ac.get("/api/workflows?status=DRAFT", headers=headers_member)
        assert res_list.status_code == 200
        assert len(res_list.json()) == 1
        assert res_list.json()[0]["id"] == wf_id

        res_list_tenant = await ac.get("/api/workflows", headers=headers_tenant)
        assert res_list_tenant.status_code == 200
        assert len(res_list_tenant.json()) == 0

        # --- Test Version APIs ---
        # A. List versions
        res_vers = await ac.get(f"/api/workflows/{wf_id}/versions", headers=headers_member)
        assert res_vers.status_code == 200
        assert len(res_vers.json()) == 1
        assert res_vers.json()[0]["version"] == 1

        # B. Create Version 2 (Member fails -> 403)
        version_payload = {
            "trigger_event_type": "PaymentReconciled",
            "conditions_data": payload["conditions_data"],
            "actions_data": payload["actions_data"]
        }
        res_mem_ver = await ac.post(f"/api/workflows/{wf_id}/versions", json=version_payload, headers=headers_member)
        assert res_mem_ver.status_code == 403

        # C. Create Version 2 (Admin succeeds -> 201)
        res_admin_ver = await ac.post(f"/api/workflows/{wf_id}/versions", json=version_payload, headers=headers_admin)
        assert res_admin_ver.status_code == 201
        assert res_admin_ver.json()["version"] == 2

        # D. Get version 2
        res_get_ver = await ac.get(f"/api/workflows/{wf_id}/versions/2", headers=headers_member)
        assert res_get_ver.status_code == 200
        assert res_get_ver.json()["trigger"]["event_type"] == "PaymentReconciled"

        # --- Test Lifecycle Routes ---
        # A. Member fails to activate version -> 403
        res_mem_act_ver = await ac.post(f"/api/workflows/{wf_id}/versions/2/activate", headers=headers_member)
        assert res_mem_act_ver.status_code == 403

        # B. Admin activates version 2 -> 200
        res_admin_act_ver = await ac.post(f"/api/workflows/{wf_id}/versions/2/activate", headers=headers_admin)
        assert res_admin_act_ver.status_code == 200

        # C. Activate workflow (Admin -> 200)
        res_act = await ac.post(f"/api/workflows/{wf_id}/activate", headers=headers_admin)
        assert res_act.status_code == 200

        # D. Verify status is active
        res_check_active = await ac.get(f"/api/workflows/{wf_id}", headers=headers_member)
        assert res_check_active.json()["status"] == "ACTIVE"
        assert res_check_active.json()["active_version_number"] == 2

        # E. Pause workflow (Owner -> 200)
        res_pause = await ac.post(f"/api/workflows/{wf_id}/pause", headers=headers_owner)
        assert res_pause.status_code == 200

        # F. Archive workflow (Owner -> 200)
        res_archive = await ac.post(f"/api/workflows/{wf_id}/archive", headers=headers_owner)
        assert res_archive.status_code == 200

        # G. Verify status is archived
        res_check_archived = await ac.get(f"/api/workflows/{wf_id}", headers=headers_member)
        assert res_check_archived.json()["status"] == "ARCHIVED"
