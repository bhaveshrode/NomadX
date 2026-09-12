import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.apps.api.main import app
from nomadx.src.domain.shared.clock.clock import Clock
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import InvoiceDbModel, PaymentAllocationDbModel, PaymentDbModel, ReceivableDbModel
from nomadx.src.infrastructure.database.repositories import PostgresReceivableRepository
from nomadx.src.domain.receivable.value_objects import ReceivableId
from nomadx.src.domain.receivable.receivable import Receivable, ReceivableCollectionStatus, ReceivablePaymentStatus
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.domain.invoice.value_objects import Money, Currency, CurrencyType
from nomadx.src.domain.invoice import CustomerId, InvoiceId


class ControllableClock(Clock):
    _time = datetime(2026, 9, 10, tzinfo=timezone.utc)

    @classmethod
    def now(cls) -> datetime:
        return cls._time

    @classmethod
    def set_time(cls, new_time: datetime):
        cls._time = new_time


@pytest.fixture(autouse=True)
def patch_clock(monkeypatch):
    monkeypatch.setattr(Clock, "now", ControllableClock.now)


@pytest.mark.asyncio
async def test_ar_reporting_customer_and_statement_flows():
    ControllableClock.set_time(datetime(2026, 9, 10))

    email_a = f"ar_merchant_a_{uuid4()}@nomadx.com"
    email_b = f"ar_merchant_b_{uuid4()}@nomadx.com"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Onboard Merchant A
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Merchant A",
            "organization_name": "Org A Corp",
            "organization_slug": f"ar-org-a-{uuid4()}",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        
        login_a = await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })
        token_a = login_a.json()["token"]

        ctx_res_a = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})
        org_a_id = ctx_res_a.json()["organization_id"]

        # 2. Onboard Merchant B
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Merchant B",
            "organization_name": "Org B Corp",
            "organization_slug": f"ar-org-b-{uuid4()}",
            "default_currency": "EUR"
        })
        assert reg_b.status_code == 201

        login_b = await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })
        token_b = login_b.json()["token"]

        # Setup Customer & activity for Org A
        cust_id = str(uuid4())
        currency = Currency("USD", CurrencyType.FIAT)

        async with session_manager.session() as session:
            # Seed Invoices
            inv1 = InvoiceDbModel(
                id=str(uuid4()),
                organization_id=org_a_id,
                invoice_number="INV-A-1",
                customer_id=cust_id,
                origin_type="NATIVE",
                currency_code="USD",
                currency_type="FIAT",
                status="ISSUED",
                grand_total="10000.00",
                subtotal="10000.00",
                discount_total="0.00",
                charge_total="0.00",
                tax_total="0.00",
                payment_terms_type="NET_30",
                due_at=datetime(2026, 8, 30), # Overdue
                issued_at=datetime(2026, 8, 1),
                created_at=datetime(2026, 8, 1),
                updated_at=datetime(2026, 8, 1)
            )
            inv2 = InvoiceDbModel(
                id=str(uuid4()),
                organization_id=org_a_id,
                invoice_number="INV-A-2",
                customer_id=cust_id,
                origin_type="NATIVE",
                currency_code="USD",
                currency_type="FIAT",
                status="ISSUED",
                grand_total="5000.00",
                subtotal="5000.00",
                discount_total="0.00",
                charge_total="0.00",
                tax_total="0.00",
                payment_terms_type="NET_30",
                due_at=datetime(2026, 9, 15), # Current
                issued_at=datetime(2026, 8, 15),
                created_at=datetime(2026, 8, 15),
                updated_at=datetime(2026, 8, 15)
            )
            session.add_all([inv1, inv2])

            # Seed Receivables (matching invoices)
            rec1 = ReceivableDbModel(
                id=str(uuid4()),
                organization_id=org_a_id,
                customer_id=cust_id,
                invoice_id=inv1.id,
                reference="AR-REC1",
                customer_reference="CUST-CUST1",
                invoice_reference="INV-INV1",
                currency="USD",
                original_amount="10000.00",
                allocated_amount="3000.00",
                outstanding_amount="7000.00",
                due_date=datetime(2026, 8, 30),
                status="PARTIALLY_PAID",
                aging_bucket="DAYS_1_30",
                dunning_stage="NONE",
                dunning_attempt_count=0,
                next_dunning_at=None,
                idempotency_key="idem-rec1",
                paid_amount="3000.00", # partially paid
                payment_status="PARTIALLY_PAID",
                collection_status="OVERDUE",
                created_at=datetime(2026, 8, 1),
                updated_at=datetime(2026, 8, 1)
            )
            rec2 = ReceivableDbModel(
                id=str(uuid4()),
                organization_id=org_a_id,
                customer_id=cust_id,
                invoice_id=inv2.id,
                reference="AR-REC2",
                customer_reference="CUST-CUST1",
                invoice_reference="INV-INV2",
                currency="USD",
                original_amount="5000.00",
                allocated_amount="0.00",
                outstanding_amount="5000.00",
                due_date=datetime(2026, 9, 15),
                status="OPEN",
                aging_bucket="CURRENT",
                dunning_stage="NONE",
                dunning_attempt_count=0,
                next_dunning_at=None,
                idempotency_key="idem-rec2",
                paid_amount="0.00",
                payment_status="UNPAID",
                collection_status="CURRENT",
                created_at=datetime(2026, 8, 15),
                updated_at=datetime(2026, 8, 15)
            )
            session.add_all([rec1, rec2])

            # Seed Payments and Allocations
            pay1 = PaymentDbModel(
                id=str(uuid4()),
                organization_id=org_a_id,
                observed_amount="3000.00",
                asset_code="USD",
                asset_type="FIAT",
                asset_decimals=2,
                network="STRIPE",
                transaction_reference="ch_12345",
                sender="Acme Corp",
                receiver="Org A Corp",
                status="CONFIRMED",
                reconciliation_status="RECONCILED",
                observed_at=datetime(2026, 8, 5),
                created_at=datetime(2026, 8, 5),
                updated_at=datetime(2026, 8, 5)
            )
            session.add(pay1)
            await session.flush()

            alloc1 = PaymentAllocationDbModel(
                id=str(uuid4()),
                payment_id=pay1.id,
                invoice_id=inv1.id,
                organization_id=org_a_id,
                allocated_amount="3000.00",
                currency="USD",
                status="APPLIED",
                created_at=datetime(2026, 8, 5),
                updated_at=datetime(2026, 8, 5)
            )
            session.add(alloc1)
            await session.commit()

        # 3. Test API Endpoint: Customer Summary
        res_summary = await ac.get(f"/api/customers/{cust_id}/receivables/summary", headers={"Authorization": f"Bearer {token_a}"})
        assert res_summary.status_code == 200
        summary = res_summary.json()
        assert summary["total_invoiced"] == "15000.00"
        assert summary["total_paid"] == "3000.00"
        assert summary["total_outstanding"] == "12000.00"
        assert summary["current"] == "5000.00"
        assert summary["overdue"] == "7000.00"

        # 4. Test API Endpoint: Customer Aging
        res_aging = await ac.get(f"/api/customers/{cust_id}/receivables/aging", headers={"Authorization": f"Bearer {token_a}"})
        assert res_aging.status_code == 200
        aging = res_aging.json()
        assert aging["current"] == "5000.00"
        assert aging["days_1_30"] == "7000.00" # Aug 30 to Sep 10 is 11 days overdue
        assert aging["total_outstanding"] == "12000.00"

        # 5. Test API Endpoint: Customer Statement (Period: Aug 1 to Sep 1)
        res_statement = await ac.get(
            f"/api/customers/{cust_id}/statement?from=2026-08-01&to=2026-09-01&limit=50&offset=0",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert res_statement.status_code == 200
        statement = res_statement.json()
        assert statement["opening_balance"] == "0.00"
        assert statement["invoices_total"] == "15000.00" # INV-A-1 + INV-A-2
        assert statement["payments_total"] == "3000.00"  # alloc1
        assert statement["closing_balance"] == "12000.00"
        assert statement["reconciled"] is True

        # Check entries chronologically
        entries = statement["entries"]
        assert len(entries) == 3
        # Entry 1: Invoice 1
        assert entries[0]["type"] == "INVOICE"
        assert entries[0]["debit"] == "10000.00"
        assert entries[0]["running_balance"] == "10000.00"
        # Entry 2: Payment allocation
        assert entries[1]["type"] == "PAYMENT"
        assert entries[1]["credit"] == "3000.00"
        assert entries[1]["running_balance"] == "7000.00"
        # Entry 3: Invoice 2
        assert entries[2]["type"] == "INVOICE"
        assert entries[2]["debit"] == "5000.00"
        assert entries[2]["running_balance"] == "12000.00"

        # 6. Test API Endpoint: Customer Statement with Date Filtering (Period: Aug 10 to Sep 1)
        # Excludes INV-A-1 (issued Aug 1) and payment (Aug 5)
        # Opening balance should represent INV-A-1 ($10,000) - Payment ($3,000) = $7,000
        res_filtered = await ac.get(
            f"/api/customers/{cust_id}/statement?from=2026-08-10&to=2026-09-01&limit=50&offset=0",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert res_filtered.status_code == 200
        statement_filtered = res_filtered.json()
        assert statement_filtered["opening_balance"] == "7000.00"
        assert statement_filtered["invoices_total"] == "5000.00"
        assert statement_filtered["payments_total"] == "0.00"
        assert statement_filtered["closing_balance"] == "12000.00"
        assert len(statement_filtered["entries"]) == 1
        assert statement_filtered["entries"][0]["reference"] == "INV-A-2"

        # 7. Test API Endpoint: Org-wide dashboard summary
        res_dash = await ac.get("/api/receivables/summary", headers={"Authorization": f"Bearer {token_a}"})
        assert res_dash.status_code == 200
        dash = res_dash.json()
        assert dash["total_outstanding"] == "12000.00"
        assert dash["current"] == "5000.00"
        assert dash["overdue"] == "7000.00"
        assert dash["invoices_outstanding"] == 2
        assert dash["customers_with_balance"] == 1

        # 8. Test API Endpoint: Outstanding invoices
        res_outstanding = await ac.get("/api/receivables/outstanding", headers={"Authorization": f"Bearer {token_a}"})
        assert res_outstanding.status_code == 200
        outs = res_outstanding.json()
        assert len(outs) == 2
        assert outs[0]["invoice_number"] == "INV-A-1"
        assert outs[0]["outstanding_amount"] == "7000.00"

        # 9. Tenant Boundary/Isolation Check: Merchant B attempts to access Merchant A's customer
        res_b_forbidden = await ac.get(f"/api/customers/{cust_id}/statement?from=2026-08-01&to=2026-09-01", headers={"Authorization": f"Bearer {token_b}"})
        assert res_b_forbidden.status_code == 404

        res_b_forbidden_summary = await ac.get(f"/api/customers/{cust_id}/receivables/summary", headers={"Authorization": f"Bearer {token_b}"})
        assert res_b_forbidden_summary.status_code == 404

        res_b_forbidden_aging = await ac.get(f"/api/customers/{cust_id}/receivables/aging", headers={"Authorization": f"Bearer {token_b}"})
        assert res_b_forbidden_aging.status_code == 404


@pytest.mark.asyncio
async def test_ar_reporting_pagination():
    ControllableClock.set_time(datetime(2026, 9, 10))

    email = f"ar_pagination_{uuid4()}@nomadx.com"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Merchant Pagination",
            "organization_name": "Pagination Corp",
            "organization_slug": f"ar-pagination-{uuid4()}",
            "default_currency": "USD"
        })
        assert reg.status_code == 201
        
        login = await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })
        token = login.json()["token"]

        ctx_res = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token}"})
        org_id = ctx_res.json()["organization_id"]

        cust_id = str(uuid4())

        # Seed 5 invoices
        async with session_manager.session() as session:
            for idx in range(1, 6):
                inv = InvoiceDbModel(
                    id=str(uuid4()),
                    organization_id=org_id,
                    invoice_number=f"INV-P-{idx}",
                    customer_id=cust_id,
                    origin_type="NATIVE",
                    currency_code="USD",
                    currency_type="FIAT",
                    status="ISSUED",
                    grand_total="1000.00",
                    subtotal="1000.00",
                    discount_total="0.00",
                    charge_total="0.00",
                    tax_total="0.00",
                    payment_terms_type="NET_30",
                    due_at=datetime(2026, 8, 30),
                    issued_at=datetime(2026, 8, idx),
                    created_at=datetime(2026, 8, idx),
                    updated_at=datetime(2026, 8, idx)
                )
                session.add(inv)
            await session.commit()

        # 1. Fetch statement with limit=2, offset=0
        res1 = await ac.get(
            f"/api/customers/{cust_id}/statement?from=2026-08-01&to=2026-09-01&limit=2&offset=0",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res1.status_code == 200
        stmt1 = res1.json()
        assert len(stmt1["entries"]) == 2
        assert stmt1["entries"][0]["reference"] == "INV-P-1"
        assert stmt1["entries"][1]["reference"] == "INV-P-2"
        # Running balances should represent cumulative balance even if sliced
        assert stmt1["entries"][0]["running_balance"] == "1000.00"
        assert stmt1["entries"][1]["running_balance"] == "2000.00"

        # 2. Fetch statement with limit=2, offset=2
        res2 = await ac.get(
            f"/api/customers/{cust_id}/statement?from=2026-08-01&to=2026-09-01&limit=2&offset=2",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res2.status_code == 200
        stmt2 = res2.json()
        assert len(stmt2["entries"]) == 2
        assert stmt2["entries"][0]["reference"] == "INV-P-3"
        assert stmt2["entries"][1]["reference"] == "INV-P-4"
        assert stmt2["entries"][0]["running_balance"] == "3000.00"
        assert stmt2["entries"][1]["running_balance"] == "4000.00"
