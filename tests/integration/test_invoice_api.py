import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from nomadx.apps.api.main import app


@pytest.mark.asyncio
async def test_invoice_api_contract_and_operations():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Onboard Organization A
        reg_a = await ac.post("/auth/register", json={
            "email": "merchant_a@nomadx.com",
            "password": "strongpassword123",
            "name": "Merchant A",
            "organization_name": "Org A Corp",
            "organization_slug": "org-a-corp",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        token_a = (await ac.post("/auth/login", json={
            "email": "merchant_a@nomadx.com",
            "password": "strongpassword123"
        })).json()["token"]

        # 2. Onboard Organization B
        reg_b = await ac.post("/auth/register", json={
            "email": "merchant_b@nomadx.com",
            "password": "strongpassword123",
            "name": "Merchant B",
            "organization_name": "Org B Corp",
            "organization_slug": "org-b-corp",
            "default_currency": "EUR"
        })
        assert reg_b.status_code == 201
        token_b = (await ac.post("/auth/login", json={
            "email": "merchant_b@nomadx.com",
            "password": "strongpassword123"
        })).json()["token"]

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 3. Create Draft Invoice for Org A
        inv_id = str(uuid4())
        create_payload = {
            "invoice_id": inv_id,
            "origin_type": "NATIVE",
            "currency_code": "USD",
            "currency_type": "FIAT",
            "payment_terms_type": "NET_30",
            "customer_id": str(uuid4()),
            "billing_snapshot": {
                "legal_name": "Target Buyer",
                "email": "buyer@target.com",
                "address": "100 Broadway",
                "country": "US",
                "tax_identifier": "TAX-A"
            }
        }
        res_create = await ac.post("/api/invoices", json=create_payload, headers=headers_a)
        assert res_create.status_code == 201
        
        dto = res_create.json()
        assert dto["id"] == inv_id
        assert dto["lifecycle_status"] == "DRAFT"
        assert dto["totals"]["grand_total"] == "0.00"

        # Try to retrieve it from Org B (should get 404 to avoid leaking existence)
        res_get_b = await ac.get(f"/api/invoices/{inv_id}", headers=headers_b)
        assert res_get_b.status_code == 404

        # Retrieve from Org A
        res_get_a = await ac.get(f"/api/invoices/{inv_id}", headers=headers_a)
        assert res_get_a.status_code == 200
        assert res_get_a.json()["billing_snapshot"]["legal_name"] == "Target Buyer"

        # 4. Add Line Item to Org A's invoice
        item_id = str(uuid4())
        line_payload = {
            "line_item_id": item_id,
            "description": "Integration Consulting Services",
            "quantity": "10",
            "unit_price": "150.00",
            "discount": "50.00",
            "tax": "25.00"
        }
        # Add from B (should fail with 404 because not found under B's context)
        res_add_b = await ac.post(f"/api/invoices/{inv_id}/line-items", json=line_payload, headers=headers_b)
        assert res_add_b.status_code == 404

        # Add from A
        res_add_a = await ac.post(f"/api/invoices/{inv_id}/line-items", json=line_payload, headers=headers_a)
        assert res_add_a.status_code == 200
        dto = res_add_a.json()
        # Item amount: 10 * 150 - 50 = 1450.00
        # Total: subtotal 1450.00 + tax 25.00 = 1475.00
        assert dto["line_items"][0]["amount"] == "1450.00"
        assert dto["totals"]["subtotal"] == "1450.00"
        assert dto["totals"]["tax_total"] == "25.00"
        assert dto["totals"]["grand_total"] == "1475.00"

        # 5. Patch Line Item
        patch_payload = {
            "description": "Updated Integration Services",
            "quantity": "5",
            "unit_price": "200.00",
            "discount": "10.00",
            "tax": "5.00"
        }
        res_patch = await ac.patch(f"/api/invoices/{inv_id}/line-items/{item_id}", json=patch_payload, headers=headers_a)
        assert res_patch.status_code == 200
        dto = res_patch.json()
        # Item amount: 5 * 200 - 10 = 990.00
        # Total: subtotal 990.00 + tax 5.00 = 995.00
        assert dto["line_items"][0]["amount"] == "990.00"
        assert dto["totals"]["grand_total"] == "995.00"

        # 6. Add Custom Tax Line
        tax_payload = {
            "code": "CGST",
            "name": "Central Goods and Services Tax",
            "rate": "0.05",
            "taxable_amount": "990.00",
            "amount": "49.50"
        }
        res_tax = await ac.post(f"/api/invoices/{inv_id}/taxes", json=tax_payload, headers=headers_a)
        assert res_tax.status_code == 200
        dto = res_tax.json()
        assert len(dto["taxes"]) == 1
        assert dto["totals"]["tax_total"] == "54.50" # 5.00 (from item) + 49.50 (from tax line) = 54.50
        assert dto["totals"]["grand_total"] == "1044.50" # 990.00 + 54.50 = 1044.50

        # 7. Add Adjustment
        adj_payload = {
            "type": "DISCOUNT",
            "description": "Volume Discount",
            "amount": "100.00"
        }
        res_adj = await ac.post(f"/api/invoices/{inv_id}/adjustments", json=adj_payload, headers=headers_a)
        assert res_adj.status_code == 200
        dto = res_adj.json()
        assert dto["totals"]["discount_total"] == "100.00"
        assert dto["totals"]["grand_total"] == "944.50" # 990.00 - 100.00 + 54.50 = 944.50

        # 8. Issue Invoice
        issue_payload = {
            "invoice_number": "INV-A-1",
            "issued_at": "2026-08-15T12:00:00"
        }
        # Issue from B (rejected)
        res_issue_b = await ac.post(f"/api/invoices/{inv_id}/issue", json=issue_payload, headers=headers_b)
        assert res_issue_b.status_code == 404

        # Issue from A (success)
        res_issue_a = await ac.post(f"/api/invoices/{inv_id}/issue", json=issue_payload, headers=headers_a)
        assert res_issue_a.status_code == 200
        assert res_issue_a.json()["lifecycle_status"] == "ISSUED"
        assert res_issue_a.json()["invoice_number"] == "INV-A-1"

        # 9. Verify editing issued invoice is blocked
        res_edit_blocked = await ac.post(f"/api/invoices/{inv_id}/line-items", json=line_payload, headers=headers_a)
        assert res_edit_blocked.status_code == 409

        # 10. List Invoices scoping
        # Org A list (should show Org A's invoice)
        list_a = await ac.get("/api/invoices", headers=headers_a)
        assert list_a.status_code == 200
        assert len(list_a.json()) == 1
        assert list_a.json()[0]["id"] == inv_id

        # Org B list (should show empty list because B has no invoices)
        list_b = await ac.get("/api/invoices", headers=headers_b)
        assert list_b.status_code == 200
        assert len(list_b.json()) == 0

        # 11. Void Invoice
        void_payload = {
            "voided_at": "2026-08-15T13:00:00"
        }
        # Void from B (rejected)
        res_void_b = await ac.post(f"/api/invoices/{inv_id}/void", json=void_payload, headers=headers_b)
        assert res_void_b.status_code == 404

        # Void from A (success)
        res_void_a = await ac.post(f"/api/invoices/{inv_id}/void", json=void_payload, headers=headers_a)
        assert res_void_a.status_code == 200
        assert res_void_a.json()["lifecycle_status"] == "VOIDED"
