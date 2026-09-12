import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.models import (
    UserDbModel,
    MembershipDbModel,
    InvoiceDbModel,
    ReceivableDbModel,
    PaymentAllocationDbModel
)
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.domain.invoice import InvoiceId, BillingSnapshot, CustomerId, Invoice, InvoiceOrigin, InvoiceOriginType, PaymentTerms, PaymentTermsType
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money, InvoiceNumber
from nomadx.src.domain.invoice.line_item import InvoiceLineItem, LineItemId
from nomadx.src.domain.organization.organization import Organization
from nomadx.src.domain.organization.value_objects import (
    OrganizationId,
    OrganizationName,
    OrganizationSlug,
    UserId,
    OrganizationSettings,
)
from nomadx.src.infrastructure.database.repositories import PostgresOrganizationRepository, PostgresInvoiceRepository


async def _setup_merchant_and_invoice(session: AsyncSession, org_id_str: str, inv_id_str: str, amount: Decimal = Decimal("10000.00"), customer_id_str: str = None) -> Invoice:
    org_repo = PostgresOrganizationRepository(session)
    existing_org = await org_repo.find_by_id(OrganizationId(org_id_str))
    if not existing_org:
        org = Organization.create(
            id=OrganizationId(org_id_str),
            name=OrganizationName("AR API Corp"),
            slug=OrganizationSlug(f"ar-api-{org_id_str[:8]}"),
            owner_id=UserId(str(uuid4())),
            settings=OrganizationSettings("USDC")
        ).value
        await org_repo.save(org)

    invoice_repo = PostgresInvoiceRepository(session)
    currency = Currency("USDC", CurrencyType.DIGITAL_ASSET)
    
    invoice = Invoice.create(
        id=InvoiceId(inv_id_str),
        organization_id=OrganizationId(org_id_str),
        origin=InvoiceOrigin(InvoiceOriginType.NATIVE),
        customer_id=CustomerId(customer_id_str) if customer_id_str else CustomerId(str(uuid4())),
        currency=currency,
        payment_terms=PaymentTerms(PaymentTermsType.NET_30),
        billing_snapshot=BillingSnapshot(
            legal_name="Client Corp",
            email="client@corp.com",
            address="456 client ave",
            country="US"
        )
    ).value
    
    line_item = InvoiceLineItem(
        id=LineItemId(str(uuid4())),
        description="Consulting Services",
        quantity=Decimal("1"),
        unit_price=Money(amount, currency)
    )
    invoice.add_line_item(line_item)
    issue_res = invoice.issue(InvoiceNumber(f"INV-{org_id_str[:8]}-{inv_id_str[:8]}"), datetime.utcnow())
    assert issue_res.is_success, f"Failed to issue invoice: {issue_res.error.message}"
    await invoice_repo.save(invoice)
    await session.flush()
    return invoice


@pytest.mark.asyncio
async def test_receivable_api_flow(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Onboard Owner (User A)
        reg_owner = await ac.post("/auth/register", json={
            "email": "owner-ar@nomadx.com",
            "password": "strongpassword123",
            "name": "Owner AR",
            "organization_name": "Org A AR",
            "organization_slug": "org-a-ar",
            "default_currency": "USDC"
        })
        assert reg_owner.status_code == 201

        login_owner = await ac.post("/auth/login", json={
            "email": "owner-ar@nomadx.com",
            "password": "strongpassword123"
        })
        token_owner = login_owner.json()["token"]
        
        ctx_owner = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_owner}"})
        org_id = ctx_owner.json()["organization_id"]

        # Setup Native Invoice
        customer_uuid = str(uuid4())
        inv_id = str(uuid4())
        await _setup_merchant_and_invoice(db_session, org_id, inv_id, Decimal("10000.00"), customer_uuid)
        await db_session.commit()

        # Onboard Admin (User B) & Member (User C) under Org A
        reg_admin = await ac.post("/auth/register", json={
            "email": "admin-ar@nomadx.com",
            "password": "strongpassword123",
            "name": "Admin AR",
            "organization_name": "Org B AR",
            "organization_slug": "org-b-ar",
            "default_currency": "USDC"
        })
        assert reg_admin.status_code == 201

        reg_member = await ac.post("/auth/register", json={
            "email": "member-ar@nomadx.com",
            "password": "strongpassword123",
            "name": "Member AR",
            "organization_name": "Org C AR",
            "organization_slug": "org-c-ar",
            "default_currency": "USDC"
        })
        assert reg_member.status_code == 201

        db_session.expire_all()
        admin_user = (await db_session.execute(select(UserDbModel).where(UserDbModel.email == "admin-ar@nomadx.com"))).scalar_one()
        member_user = (await db_session.execute(select(UserDbModel).where(UserDbModel.email == "member-ar@nomadx.com"))).scalar_one()

        admin_membership = MembershipDbModel(organization_id=org_id, user_id=admin_user.id, role="ADMIN", status="ACTIVE")
        member_membership = MembershipDbModel(organization_id=org_id, user_id=member_user.id, role="MEMBER", status="ACTIVE")
        db_session.add(admin_membership)
        db_session.add(member_membership)
        await db_session.commit()

        # Log in
        login_admin = await ac.post("/auth/login", json={"email": "admin-ar@nomadx.com", "password": "strongpassword123"})
        token_admin = login_admin.json()["token"]

        login_member = await ac.post("/auth/login", json={"email": "member-ar@nomadx.com", "password": "strongpassword123"})
        token_member = login_member.json()["token"]

        # Headers
        headers_owner = {"Authorization": f"Bearer {token_owner}", "X-Organization-ID": org_id}
        headers_admin = {"Authorization": f"Bearer {token_admin}", "X-Organization-ID": org_id}
        headers_member = {"Authorization": f"Bearer {token_member}", "X-Organization-ID": org_id}

        # Onboard Tenant D (Org D) for isolation
        reg_tenant = await ac.post("/auth/register", json={
            "email": "tenant-ar@nomadx.com",
            "password": "strongpassword123",
            "name": "Tenant AR",
            "organization_name": "Org D AR",
            "organization_slug": "org-d-ar",
            "default_currency": "USDC"
        })
        assert reg_tenant.status_code == 201

        login_tenant = await ac.post("/auth/login", json={"email": "tenant-ar@nomadx.com", "password": "strongpassword123"})
        token_tenant = login_tenant.json()["token"]
        ctx_tenant = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_tenant}"})
        org_tenant_id = ctx_tenant.json()["organization_id"]
        headers_tenant = {"Authorization": f"Bearer {token_tenant}", "X-Organization-ID": org_tenant_id}

        # --- Test Receivable Creation ---
        rec_id = str(uuid4())
        payload = {
            "receivable_id": rec_id,
            "invoice_id": inv_id
        }

        # A. Member attempts creation -> 403 Forbidden
        res_mem_create = await ac.post("/api/receivables", json=payload, headers=headers_member)
        assert res_mem_create.status_code == 403

        # B. Admin creates receivable -> 201 Created
        headers_admin_idem = {**headers_admin, "Idempotency-Key": "idem-rec-api-1"}
        res_admin_create = await ac.post("/api/receivables", json=payload, headers=headers_admin_idem)
        assert res_admin_create.status_code == 201
        assert res_admin_create.json()["id"] == rec_id

        # C. Idempotent Retry -> 200 OK
        res_admin_repeat = await ac.post("/api/receivables", json=payload, headers=headers_admin_idem)
        assert res_admin_repeat.status_code == 200
        assert res_admin_repeat.json()["id"] == rec_id
        cust_ref = res_admin_repeat.json()["customer_reference"]

        # --- Test Receivable Details & Isolation ---
        # A. Member can view detail
        res_detail = await ac.get(f"/api/receivables/{rec_id}", headers=headers_member)
        assert res_detail.status_code == 200
        assert res_detail.json()["outstanding_amount"] == "10000.00"

        # B. Org D tenant attempts detail lookup -> 404 Not Found
        res_tenant_detail = await ac.get(f"/api/receivables/{rec_id}", headers=headers_tenant)
        assert res_tenant_detail.status_code == 404

        # --- Test Payment Allocation ---
        pay_id = str(uuid4())
        alloc_ref = str(uuid4())

        # Seed payment allocation record in DB
        db_alloc = PaymentAllocationDbModel(
            id=alloc_ref,
            payment_id=pay_id,
            invoice_id=inv_id,
            allocated_amount="2000.00",
            currency="USDC",
            status="ALLOCATED",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            organization_id=org_id
        )
        db_session.add(db_alloc)
        await db_session.commit()

        alloc_payload = {
            "payment_id": pay_id,
            "allocated_amount": 2000.00,
            "currency_code": "USDC",
            "allocation_reference": alloc_ref
        }

        # A. Member attempts allocation -> 403 Forbidden
        res_mem_alloc = await ac.post(f"/api/receivables/{rec_id}/allocations", json=alloc_payload, headers=headers_member)
        assert res_mem_alloc.status_code == 403

        # B. Admin allocates payment -> 200 OK
        res_admin_alloc = await ac.post(f"/api/receivables/{rec_id}/allocations", json=alloc_payload, headers=headers_admin)
        assert res_admin_alloc.status_code == 200
        assert res_admin_alloc.json()["outstanding_amount"] == "8000.00"

        # C. Idempotent Retry -> 200 OK (no duplicate allocation)
        res_admin_alloc_dup = await ac.post(f"/api/receivables/{rec_id}/allocations", json=alloc_payload, headers=headers_admin)
        assert res_admin_alloc_dup.status_code == 200
        assert res_admin_alloc_dup.json()["outstanding_amount"] == "8000.00"

        # D. Allocation over limit fails -> 409 Conflict
        alloc_ref_large = str(uuid4())
        alloc_large_payload = {
            "payment_id": str(uuid4()),
            "allocated_amount": 15000.00,
            "currency_code": "USDC",
            "allocation_reference": alloc_ref_large
        }
        res_large_alloc = await ac.post(f"/api/receivables/{rec_id}/allocations", json=alloc_large_payload, headers=headers_admin)
        assert res_large_alloc.status_code == 409

        # --- Test Mark Overdue ---
        # Force the receivable's due date in the database to be in the past to trigger overdue state
        db_rec = await db_session.get(ReceivableDbModel, rec_id)
        db_rec.due_date = datetime.utcnow() - timedelta(days=5)
        await db_session.commit()

        res_overdue = await ac.post(f"/api/receivables/{rec_id}/overdue", headers=headers_admin)
        assert res_overdue.status_code == 200
        assert res_overdue.json()["collection_status"] == "OVERDUE"

        # --- Test Settle (Fail first then succeed) ---
        # Settle fails because outstanding amount > 0
        res_settle_fail = await ac.post(f"/api/receivables/{rec_id}/settle", headers=headers_admin)
        assert res_settle_fail.status_code == 409

        # Allocate the remaining $8,000
        alloc_ref_remaining = str(uuid4())
        rem_pay_id = str(uuid4())
        db_alloc_rem = PaymentAllocationDbModel(
            id=alloc_ref_remaining,
            payment_id=rem_pay_id,
            invoice_id=inv_id,
            allocated_amount="8000.00",
            currency="USDC",
            status="ALLOCATED",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            organization_id=org_id
        )
        db_session.add(db_alloc_rem)
        await db_session.commit()

        rem_payload = {
            "payment_id": rem_pay_id,
            "allocated_amount": 8000.00,
            "currency_code": "USDC",
            "allocation_reference": alloc_ref_remaining
        }
        await ac.post(f"/api/receivables/{rec_id}/allocations", json=rem_payload, headers=headers_admin)

        # Settle succeeds now
        res_settle_success = await ac.post(f"/api/receivables/{rec_id}/settle", headers=headers_admin)
        assert res_settle_success.status_code == 200
        assert res_settle_success.json()["status"] == "SETTLED"

        # --- Test Write-off and Mutation Prevention ---
        # Create second receivable
        inv_id2 = str(uuid4())
        await _setup_merchant_and_invoice(db_session, org_id, inv_id2, Decimal("5000.00"), customer_uuid)
        await db_session.commit()

        rec_id2 = str(uuid4())
        await ac.post("/api/receivables", json={"receivable_id": rec_id2, "invoice_id": inv_id2}, headers=headers_admin)

        # Write off
        res_writeoff = await ac.post(f"/api/receivables/{rec_id2}/write-off", json={"reason": "bad debt"}, headers=headers_admin)
        assert res_writeoff.status_code == 200, f"Writeoff failed: {res_writeoff.status_code} - {res_writeoff.text}"
        assert res_writeoff.json()["status"] == "WRITTEN_OFF"

        # Mutation attempt on written off receivable fails -> 409
        alloc_ref_fail = str(uuid4())
        fail_pay_id = str(uuid4())
        db_alloc_fail = PaymentAllocationDbModel(
            id=alloc_ref_fail,
            payment_id=fail_pay_id,
            invoice_id=inv_id2,
            allocated_amount="1000.00",
            currency="USDC",
            status="ALLOCATED",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            organization_id=org_id
        )
        db_session.add(db_alloc_fail)
        await db_session.commit()

        fail_payload = {
            "payment_id": fail_pay_id,
            "allocated_amount": 1000.00,
            "currency_code": "USDC",
            "allocation_reference": alloc_ref_fail
        }
        res_mut_fail = await ac.post(f"/api/receivables/{rec_id2}/allocations", json=fail_payload, headers=headers_admin)
        assert res_mut_fail.status_code == 409

        # --- Test Dunning Progression ---
        res_dunning = await ac.post(
            f"/api/receivables/{rec_id2}/dunning/advance",
            json={"next_stage": "FIRST_REMINDER"},
            headers=headers_admin
        )
        # Cannot advance a written-off receivable
        assert res_dunning.status_code == 409

        # Test dunning lookup
        res_dunning_get = await ac.get(f"/api/receivables/{rec_id2}/dunning", headers=headers_member)
        assert res_dunning_get.status_code == 200
        assert res_dunning_get.json()["collection_status"] == "CURRENT"

        # --- Test Customer Statement & Aging ---
        # Customer statement
        res_stmt = await ac.get(f"/api/customers/{cust_ref}/receivables", headers=headers_member)
        assert res_stmt.status_code == 200
        assert res_stmt.json()["total_receivables"] == "15000.00" # 10000 + 5000

        # Aging summary
        res_aging = await ac.get("/api/receivables/aging-summary", headers=headers_member)
        assert res_aging.status_code == 200
        assert "current" in res_aging.json()
