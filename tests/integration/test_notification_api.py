import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from sqlalchemy import select, update

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import MembershipDbModel, NotificationDbModel


@pytest.mark.asyncio
async def test_notification_api_creation_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & Login Merchant A (OWNER role)
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_notif_a@example.com",
            "password": "strongpassword123",
            "name": "Owner Notif A",
            "organization_name": "Org Notif A",
            "organization_slug": "org-notif-a",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        token_a = (await ac.post("/auth/login", json={"email": "owner_notif_a@example.com", "password": "strongpassword123"})).json()["token"]
        
        ctx_a = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})).json()
        org_a_id = ctx_a["organization_id"]

        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # 2. Register & Login Merchant B (OWNER of another organization)
        reg_b = await ac.post("/auth/register", json={
            "email": "owner_notif_b@example.com",
            "password": "strongpassword123",
            "name": "Owner Notif B",
            "organization_name": "Org Notif B",
            "organization_slug": "org-notif-b",
            "default_currency": "USD"
        })
        assert reg_b.status_code == 201
        token_b = (await ac.post("/auth/login", json={"email": "owner_notif_b@example.com", "password": "strongpassword123"})).json()["token"]
        
        ctx_b = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_b}"})).json()
        org_b_id = ctx_b["organization_id"]
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Organization-ID": org_b_id
        }

        # --- Test Cases ---

        # 1. Successful Creation (OWNER role)
        res_create = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "recipient@example.com",
            "subject": "Invoice Paid",
            "template_id": "invoice_paid_template",
            "payload": {"amount": "100.00"},
            "priority": "HIGH",
            "reference": "NOT-REF-001",
            "idempotency_key": "idem-api-notif-1",
            "source_event_id": "EVT-123",
            "source_event_type": "InvoiceIssued"
        }, headers=headers_a)
        assert res_create.status_code == 201
        data = res_create.json()
        assert data["status"] == "PENDING"
        assert data["priority"] == "HIGH"
        assert data["recipient"] == "recipient@example.com"

        # 2. Duplicate Idempotency Key returns 200 OK with existing notification
        res_dup = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "recipient@example.com",
            "subject": "Invoice Paid",
            "template_id": "invoice_paid_template",
            "payload": {"amount": "100.00"},
            "priority": "HIGH",
            "reference": "NOT-REF-001",
            "idempotency_key": "idem-api-notif-1"
        }, headers=headers_a)
        assert res_dup.status_code == 200
        assert res_dup.json()["id"] == data["id"]

        # 3. Duplicate Source Event returns 200 OK with existing notification
        res_dup_src = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "recipient@example.com",
            "subject": "Invoice Paid",
            "template_id": "invoice_paid_template",
            "payload": {"amount": "100.00"},
            "priority": "HIGH",
            "reference": "NOT-REF-002",
            "idempotency_key": "idem-api-notif-2",
            "source_event_id": "EVT-123",
            "source_event_type": "InvoiceIssued"
        }, headers=headers_a)
        assert res_dup_src.status_code == 200
        assert res_dup_src.json()["id"] == data["id"]


@pytest.mark.asyncio
async def test_notification_api_idempotency_headers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_notif_headers@example.com",
            "password": "strongpassword123",
            "name": "Owner Notif Headers",
            "organization_name": "Org Notif Headers",
            "organization_slug": "org-notif-headers",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_notif_headers@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # 1. Create using Idempotency-Key Header
        headers_idemp = {**headers_a, "Idempotency-Key": "idem-header-notif-1"}
        res1 = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "header@example.com",
            "subject": "Invoice Due",
            "template_id": "invoice_due_temp",
            "payload": {"amount": "20.00"},
            "priority": "NORMAL",
            "reference": "NOT-REF-H1"
        }, headers=headers_idemp)
        assert res1.status_code == 201
        data1 = res1.json()

        # 2. Resubmit same header returns 200 OK
        res2 = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "header@example.com",
            "subject": "Invoice Due",
            "template_id": "invoice_due_temp",
            "payload": {"amount": "20.00"},
            "priority": "NORMAL",
            "reference": "NOT-REF-H1"
        }, headers=headers_idemp)
        assert res2.status_code == 200
        assert res2.json()["id"] == data1["id"]


@pytest.mark.asyncio
async def test_notification_api_listing_and_filtering():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_notif_list@example.com",
            "password": "strongpassword123",
            "name": "Owner Notif List",
            "organization_name": "Org Notif List",
            "organization_slug": "org-notif-list",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_notif_list@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # Create two notifications
        res_c1 = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "user1@example.com",
            "subject": "Sub 1",
            "template_id": "temp1",
            "reference": "NOT-L1",
            "idempotency_key": "idem-list-1"
        }, headers=headers_a)
        assert res_c1.status_code == 201

        res_c2 = await ac.post("/api/notifications", json={
            "notification_type": "PAYMENT_CONFIRMED",
            "channel": "WEBHOOK",
            "recipient": "https://callback.com",
            "subject": "Sub 2",
            "template_id": "temp2",
            "reference": "NOT-L2",
            "idempotency_key": "idem-list-2"
        }, headers=headers_a)
        assert res_c2.status_code == 201

        # 1. Scoped listing
        res_list = await ac.get("/api/notifications", headers=headers_a)
        assert res_list.status_code == 200
        assert len(res_list.json()) == 2

        # 2. Filter by status
        res_filter_status = await ac.get("/api/notifications?status=PENDING", headers=headers_a)
        assert len(res_filter_status.json()) == 2

        # 3. Filter by channel
        res_filter_channel = await ac.get("/api/notifications?channel=WEBHOOK", headers=headers_a)
        assert len(res_filter_channel.json()) == 1
        assert res_filter_channel.json()[0]["reference"] == "NOT-L2"

        # 4. Pagination
        res_paginated = await ac.get("/api/notifications?limit=1&offset=0", headers=headers_a)
        assert len(res_paginated.json()) == 1


@pytest.mark.asyncio
async def test_notification_api_detail_and_cross_tenant():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register Org A
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_det_notif_a@example.com",
            "password": "strongpassword123",
            "name": "Owner Det Notif A",
            "organization_name": "Org Det Notif A",
            "organization_slug": "org-det-notif-a",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_det_notif_a@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # Register Org B
        reg_b = await ac.post("/auth/register", json={
            "email": "owner_det_notif_b@example.com",
            "password": "strongpassword123",
            "name": "Owner Det Notif B",
            "organization_name": "Org Det Notif B",
            "organization_slug": "org-det-notif-b",
            "default_currency": "USD"
        })
        token_b = (await ac.post("/auth/login", json={"email": "owner_det_notif_b@example.com", "password": "strongpassword123"})).json()["token"]
        org_b_id = reg_b.json()["organization"]["id"]
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Organization-ID": org_b_id
        }

        # Create notification in Org A
        res_create = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "detail@example.com",
            "subject": "Subject",
            "template_id": "temp_det",
            "reference": "NOT-DET-1",
            "idempotency_key": "idem-det-1"
        }, headers=headers_a)
        nid = res_create.json()["id"]

        # 1. Retrieve Org A notification details (success)
        res_get = await ac.get(f"/api/notifications/{nid}", headers=headers_a)
        assert res_get.status_code == 200
        assert res_get.json()["reference"] == "NOT-DET-1"

        # 2. Retrieve Org A notification details from Org B context (must return 404 hidden)
        res_get_cross = await ac.get(f"/api/notifications/{nid}", headers=headers_b)
        assert res_get_cross.status_code == 404


@pytest.mark.asyncio
async def test_notification_api_lifecycle_and_failures():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_life_notif@example.com",
            "password": "strongpassword123",
            "name": "Owner Life Notif",
            "organization_name": "Org Life Notif",
            "organization_slug": "org-life-notif",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_life_notif@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # 1. Create notification
        c_res = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "life@example.com",
            "subject": "Life check",
            "template_id": "temp_life",
            "reference": "NOT-LIFE-1",
            "idempotency_key": "idem-life-1"
        }, headers=headers_a)
        nid = c_res.json()["id"]

        # 2. Start process manually
        res_proc = await ac.post(f"/api/notifications/{nid}/process", headers=headers_a)
        assert res_proc.status_code == 200
        assert res_proc.json()["status"] == "PROCESSING"

        # 3. Create second notification to cancel
        c_res2 = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "cancel@example.com",
            "subject": "Cancel check",
            "template_id": "temp_cancel",
            "reference": "NOT-LIFE-2",
            "idempotency_key": "idem-life-2"
        }, headers=headers_a)
        nid2 = c_res2.json()["id"]

        res_cancel = await ac.post(f"/api/notifications/{nid2}/cancel", headers=headers_a)
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "CANCELLED"

        # 4. Retry a FAILED notification
        # Create third notification, process it, then mark FAILED manually in DB (so we can test retry)
        c_res3 = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "retry@example.com",
            "subject": "Retry check",
            "template_id": "temp_retry",
            "reference": "NOT-LIFE-3",
            "idempotency_key": "idem-life-3"
        }, headers=headers_a)
        nid3 = c_res3.json()["id"]

        await ac.post(f"/api/notifications/{nid3}/process", headers=headers_a)

        async with session_manager.session() as session:
            await session.execute(
                update(NotificationDbModel)
                .where(NotificationDbModel.id == nid3)
                .values(
                    status="FAILED",
                    failure_code="PROVIDER_REJECTED",
                    failure_message="Gateway error",
                    failure_retryable=True
                )
            )
            await session.commit()

        # Retry it
        res_retry = await ac.post(f"/api/notifications/{nid3}/retry", headers=headers_a)
        assert res_retry.status_code == 200
        assert res_retry.json()["status"] == "RETRYING"


@pytest.mark.asyncio
async def test_notification_api_role_authorization():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register Member (OWNER by default, manually downgrade to MEMBER in DB)
        reg_member = await ac.post("/auth/register", json={
            "email": "auth_member_notif@example.com",
            "password": "strongpassword123",
            "name": "Auth Member Notif",
            "organization_name": "Org Auth Notif",
            "organization_slug": "org-auth-notif",
            "default_currency": "USD"
        })
        user_id = reg_member.json()["user"]["id"]
        org_id = reg_member.json()["organization"]["id"]
        token_member = (await ac.post("/auth/login", json={"email": "auth_member_notif@example.com", "password": "strongpassword123"})).json()["token"]
        
        # Downgrade user role to MEMBER
        async with session_manager.session() as session:
            await session.execute(
                update(MembershipDbModel)
                .where(MembershipDbModel.user_id == user_id)
                .values(role="MEMBER")
            )
            await session.commit()

        headers_member = {
            "Authorization": f"Bearer {token_member}",
            "X-Organization-ID": org_id
        }

        # 2. View actions should be allowed for MEMBER
        res_list = await ac.get("/api/notifications", headers=headers_member)
        assert res_list.status_code == 200

        # 3. Create action should be blocked for MEMBER (403 Forbidden)
        res_create = await ac.post("/api/notifications", json={
            "notification_type": "INVOICE_ISSUED",
            "channel": "EMAIL",
            "recipient": "blocked@example.com",
            "subject": "Blocked check",
            "template_id": "temp_blocked",
            "reference": "NOT-AUTH-1",
            "idempotency_key": "idem-auth-1"
        }, headers=headers_member)
        assert res_create.status_code == 403
