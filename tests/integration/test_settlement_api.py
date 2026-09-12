import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import MembershipDbModel, SettlementDbModel, OutboxEventDbModel
from nomadx.src.infrastructure.database.repositories import PostgresPaymentRepository, PostgresSettlementRepository
from nomadx.src.domain.payment import PaymentId, PaymentAsset, PaymentNetwork, Payment
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money


async def _setup_confirmed_payment(org_id: str, payment_id: str, amount: Decimal = Decimal("5000.00"), status: str = "CONFIRMED") -> None:
    async with session_manager.session() as session:
        payment_repo = PostgresPaymentRepository(session)
        currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
        observed_amount = Money(amount, currency)
        asset = PaymentAsset("USDC", "DIGITAL_ASSET", 6)
        network = PaymentNetwork("BASE", "BLOCKCHAIN")

        payment = Payment.create(
            id=PaymentId(payment_id),
            organization_id=OrganizationId(org_id),
            payment_intent_id=None,
            observed_amount=observed_amount,
            asset=asset,
            network=network,
            transaction_reference=f"0xTx_{payment_id[:8]}",
            sender="0xSenderWallet",
            receiver="0xReceiverWallet",
            observed_at=Clock.now()
        ).value

        if status == "CONFIRMED":
            payment.confirm()
        elif status == "REJECTED":
            payment.reject()

        await payment_repo.save(payment)
        await session.commit()


@pytest.mark.asyncio
async def test_settlement_api_creation_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & Login Merchant A (OWNER role by default)
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_a@example.com",
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A",
            "organization_slug": "org-a",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        token_a = (await ac.post("/auth/login", json={"email": "owner_a@example.com", "password": "strongpassword123"})).json()["token"]
        
        ctx_a = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})).json()
        org_a_id = ctx_a["organization_id"]

        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # Setup Confirmed Payment for Org A
        pay_id_a = str(uuid4())
        await _setup_confirmed_payment(org_a_id, pay_id_a, Decimal("5000.00"))

        # 2. Register & Login Member B (OWNER by default, manually downgrade to MEMBER in DB)
        reg_b = await ac.post("/auth/register", json={
            "email": "member_b@example.com",
            "password": "strongpassword123",
            "name": "Member B",
            "organization_name": "Org A Member", # Same org name is fine, will create a separate org
            "organization_slug": "org-b",
            "default_currency": "USD"
        })
        assert reg_b.status_code == 201
        user_b_id = reg_b.json()["user"]["id"]
        token_b = (await ac.post("/auth/login", json={"email": "member_b@example.com", "password": "strongpassword123"})).json()["token"]
        
        # Note: We leave B as OWNER of Org B so B has write permissions for Org B,
        # but B should still fail to settle Org A's payment with a 404 (hidden).

        ctx_b = (await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_b}"})).json()
        org_b_id = ctx_b["organization_id"]
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Organization-ID": org_b_id
        }

        # --- Test Cases ---

        # 1. Successful Creation (OWNER role)
        res_create = await ac.post("/api/settlements", json={
            "payment_id": pay_id_a,
            "amount": "2000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-REF-001",
            "idempotency_key": "idem-api-1"
        }, headers=headers_a)
        assert res_create.status_code == 201
        data = res_create.json()
        assert data["status"] == "PENDING"
        assert data["amount"] == "2000.00"

        # 2. Confirmed Payment Required (try creating from unconfirmed payment)
        pay_unconfirmed = str(uuid4())
        await _setup_confirmed_payment(org_a_id, pay_unconfirmed, Decimal("1000.00"), status="DETECTED")
        res_unconfirmed = await ac.post("/api/settlements", json={
            "payment_id": pay_unconfirmed,
            "amount": "500.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-REF-002",
            "idempotency_key": "idem-api-2"
        }, headers=headers_a)
        assert res_unconfirmed.status_code == 409
        assert "Payment status is" in res_unconfirmed.json()["detail"]

        # 3. Invalid payment rejected (non-existent ID)
        res_missing = await ac.post("/api/settlements", json={
            "payment_id": str(uuid4()),
            "amount": "500.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-REF-003",
            "idempotency_key": "idem-api-3"
        }, headers=headers_a)
        assert res_missing.status_code == 404

        # 4. Cross-tenant payment rejected (Org B tries to settle Org A's payment)
        res_cross = await ac.post("/api/settlements", json={
            "payment_id": pay_id_a,
            "amount": "500.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-REF-004",
            "idempotency_key": "idem-api-4"
        }, headers=headers_b)
        assert res_cross.status_code == 404 # hidden to prevent resource existence leakage

        # 5. Insufficient balance rejected
        res_insufficient = await ac.post("/api/settlements", json={
            "payment_id": pay_id_a,
            "amount": "4000.00", # exceeds available (5000 - 2000 = 3000)
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-REF-005",
            "idempotency_key": "idem-api-5"
        }, headers=headers_a)
        assert res_insufficient.status_code == 409
        assert "exceeds available" in res_insufficient.json()["detail"]


@pytest.mark.asyncio
async def test_settlement_api_idempotency_and_headers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_idemp@example.com",
            "password": "strongpassword123",
            "name": "Owner Idemp",
            "organization_name": "Org Idemp",
            "organization_slug": "org-idemp",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_idemp@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        pay_id = str(uuid4())
        await _setup_confirmed_payment(org_a_id, pay_id, Decimal("5000.00"))

        # 1. Create with Idempotency-Key Header
        headers_idemp = {**headers_a, "Idempotency-Key": "idem-header-123"}
        res1 = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-ID-1",
        }, headers=headers_idemp)
        assert res1.status_code == 201
        sid1 = res1.json()["id"]

        # 2. Retry with same Idempotency-Key Header -> returns 200 OK and same settlement ID
        res2 = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-ID-1-DIFF",
        }, headers=headers_idemp)
        assert res2.status_code == 200
        assert res2.json()["id"] == sid1


@pytest.mark.asyncio
async def test_settlement_api_listing_and_filtering():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_list@example.com",
            "password": "strongpassword123",
            "name": "Owner List",
            "organization_name": "Org List",
            "organization_slug": "org-list",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_list@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        pay_id = str(uuid4())
        await _setup_confirmed_payment(org_a_id, pay_id, Decimal("5000.00"))

        # Create two settlements
        await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "500.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-LIST-1",
            "idempotency_key": "idem-list-1"
        }, headers=headers_a)

        await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1500.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-LIST-2",
            "idempotency_key": "idem-list-2"
        }, headers=headers_a)

        # 1. Organization-scoped listing
        res_list = await ac.get("/api/settlements", headers=headers_a)
        assert res_list.status_code == 200
        assert len(res_list.json()) == 2

        # 2. Filter by status
        res_filter_status = await ac.get("/api/settlements?status=PENDING", headers=headers_a)
        assert res_filter_status.status_code == 200
        assert len(res_filter_status.json()) == 2

        res_filter_status_none = await ac.get("/api/settlements?status=COMPLETED", headers=headers_a)
        assert len(res_filter_status_none.json()) == 0

        # 3. Pagination
        res_paginated = await ac.get("/api/settlements?limit=1&offset=0", headers=headers_a)
        assert len(res_paginated.json()) == 1


@pytest.mark.asyncio
async def test_settlement_api_detail_and_availability():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_det@example.com",
            "password": "strongpassword123",
            "name": "Owner Det",
            "organization_name": "Org Det",
            "organization_slug": "org-det",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_det@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        # Onboard Org B
        reg_b = await ac.post("/auth/register", json={
            "email": "owner_det_b@example.com",
            "password": "strongpassword123",
            "name": "Owner Det B",
            "organization_name": "Org Det B",
            "organization_slug": "org-det-b",
            "default_currency": "USD"
        })
        token_b = (await ac.post("/auth/login", json={"email": "owner_det_b@example.com", "password": "strongpassword123"})).json()["token"]
        org_b_id = reg_b.json()["organization"]["id"]
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Organization-ID": org_b_id
        }

        pay_id = str(uuid4())
        await _setup_confirmed_payment(org_a_id, pay_id, Decimal("5000.00"))

        create_res = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1500.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-DET-1",
            "idempotency_key": "idem-det"
        }, headers=headers_a)
        sid = create_res.json()["id"]

        # 1. Retrieve own settlement
        res_get = await ac.get(f"/api/settlements/{sid}", headers=headers_a)
        assert res_get.status_code == 200
        assert res_get.json()["id"] == sid

        # 2. Cross-tenant retrieval hidden (returns 404 instead of 403)
        res_cross_get = await ac.get(f"/api/settlements/{sid}", headers=headers_b)
        assert res_cross_get.status_code == 404

        # 3. Availability check
        res_avail = await ac.get(f"/api/payments/{pay_id}/settlement-availability", headers=headers_a)
        assert res_avail.status_code == 200
        avail_data = res_avail.json()
        assert avail_data["payment_amount"] == "5000.00"
        assert avail_data["settled_amount"] == "1500.00"
        assert avail_data["available_amount"] == "3500.00"

        # 4. Payment lookup endpoint
        res_lookup = await ac.get(f"/api/payments/{pay_id}/settlements", headers=headers_a)
        assert res_lookup.status_code == 200
        assert len(res_lookup.json()["settlements"]) == 1
        assert res_lookup.json()["settlements"][0]["id"] == sid


@pytest.mark.asyncio
async def test_settlement_api_lifecycle_and_actions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_lifecycle@example.com",
            "password": "strongpassword123",
            "name": "Owner Lifecycle",
            "organization_name": "Org Lifecycle",
            "organization_slug": "org-lifecycle",
            "default_currency": "USD"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_lifecycle@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }

        pay_id = str(uuid4())
        await _setup_confirmed_payment(org_a_id, pay_id, Decimal("5000.00"))

        # Create PENDING settlement
        c_res1 = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-LIFE-1",
            "idempotency_key": "idem-life-1"
        }, headers=headers_a)
        sid1 = c_res1.json()["id"]

        # 1. Process PENDING -> status should be PROCESSING
        res_proc = await ac.post(f"/api/settlements/{sid1}/process", headers=headers_a)
        assert res_proc.status_code == 200
        assert res_proc.json()["status"] == "PROCESSING"

        # 2. Cancel a second PENDING settlement
        c_res2 = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-LIFE-2",
            "idempotency_key": "idem-life-2"
        }, headers=headers_a)
        sid2 = c_res2.json()["id"]

        res_cancel = await ac.post(f"/api/settlements/{sid2}/cancel", headers=headers_a)
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "CANCELLED"

        # 3. Retry a FAILED settlement
        # Create third settlement, process it, then mark FAILED manually in DB (so we can test retry)
        c_res3 = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "1000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-LIFE-3",
            "idempotency_key": "idem-life-3"
        }, headers=headers_a)
        sid3 = c_res3.json()["id"]

        await ac.post(f"/api/settlements/{sid3}/process", headers=headers_a)

        async with session_manager.session() as session:
            await session.execute(
                update(SettlementDbModel)
                .where(SettlementDbModel.id == sid3)
                .values(
                    status="FAILED",
                    failure_code="NETWORK_UNAVAILABLE",
                    failure_message="RPC crashed",
                    failure_retryable=True
                )
            )
            await session.commit()

        # Retry it
        res_retry = await ac.post(f"/api/settlements/{sid3}/retry", headers=headers_a)
        assert res_retry.status_code == 200
        assert res_retry.json()["status"] == "RETRYING"

        # Process RETRYING -> PROCESSING
        res_proc_retry = await ac.post(f"/api/settlements/{sid3}/process", headers=headers_a)
        assert res_proc_retry.status_code == 200
        assert res_proc_retry.json()["status"] == "PROCESSING"


@pytest.mark.asyncio
async def test_settlement_api_role_authorization():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & Login Member (OWNER by default, manually downgrade to MEMBER in DB)
        reg_member = await ac.post("/auth/register", json={
            "email": "auth_member@example.com",
            "password": "strongpassword123",
            "name": "Auth Member",
            "organization_name": "Org Auth",
            "organization_slug": "org-auth",
            "default_currency": "USD"
        })
        user_id = reg_member.json()["user"]["id"]
        org_id = reg_member.json()["organization"]["id"]
        token_member = (await ac.post("/auth/login", json={"email": "auth_member@example.com", "password": "strongpassword123"})).json()["token"]
        
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

        # Setup Confirmed Payment
        pay_id = str(uuid4())
        await _setup_confirmed_payment(org_id, pay_id, Decimal("1000.00"))

        # 2. View actions should be allowed for MEMBER
        res_avail = await ac.get(f"/api/payments/{pay_id}/settlement-availability", headers=headers_member)
        assert res_avail.status_code == 200

        # 3. Create action should be blocked for MEMBER (403 Forbidden)
        res_create = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "100.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": "SET-AUTH-1",
            "idempotency_key": "idem-auth-1"
        }, headers=headers_member)
        assert res_create.status_code == 403
        assert "not authorized" in res_create.json()["detail"]
