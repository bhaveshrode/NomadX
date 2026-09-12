import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.blockchain.clients.fake import FakeBlockchainClient
from nomadx.src.infrastructure.blockchain.reconciliation_worker import ReconciliationWorker


@pytest.mark.asyncio
async def test_end_to_end_payment_reconciliation_flow():
    # 1. Setup Fake Blockchain Client for BASE network
    fake_client = FakeBlockchainClient(current_height=1000)
    # Register mock logs & receipts for test with 11 confirmations (block 950 vs height 1000)
    usdc_contract = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    fake_client.add_simulated_transfer_log(
        block_number=950,
        tx_hash="0xTxHashReconE2E",
        token_address=usdc_contract,
        sender="0xSenderAddress",
        receiver="0xMerchantAddress",
        raw_amount=5000000000 # 5,000 USDC
    )
    fake_client.add_simulated_receipt("0xTxHashReconE2E", status="0x1", block_number=950)
    container.blockchain_provider.register_client("BASE", fake_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 2. Register Merchant & Create Invoice
        reg_res = await ac.post("/auth/register", json={
            "email": "recon_merchant@nomadx.com",
            "password": "strongpassword123",
            "name": "Recon Merchant",
            "organization_name": "Recon Org",
            "organization_slug": "recon-org",
            "default_currency": "USD"
        })
        assert reg_res.status_code == 201

        login_res = await ac.post("/auth/login", json={
            "email": "recon_merchant@nomadx.com",
            "password": "strongpassword123"
        })
        assert login_res.status_code == 200
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get Organization ID
        me_res = await ac.get("/api/secure-context", headers=headers)
        assert me_res.status_code == 200
        org_id = me_res.json()["organization_id"]

        # Create Invoice
        inv_payload = {
            "origin_type": "NATIVE",
            "currency_code": "USDC",
            "currency_type": "DIGITAL_ASSET",
            "payment_terms_type": "NET_30",
            "due_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
            "customer_id": str(uuid4()),
            "billing_snapshot": {
                "legal_name": "Buyer Corp",
                "email": "buyer@corp.com",
                "address": "123 Buyer St",
                "country": "US",
                "tax_identifier": "TAX-555"
            }
        }
        create_inv_res = await ac.post("/api/invoices", json=inv_payload, headers=headers)
        assert create_inv_res.status_code == 201
        invoice_id = create_inv_res.json()["id"]

        # Add Line Item to invoice ($5,000)
        item_payload = {
            "description": "Software License",
            "quantity": "1",
            "unit_price": "5000.00"
        }
        add_item_res = await ac.post(f"/api/invoices/{invoice_id}/line-items", json=item_payload, headers=headers)
        assert add_item_res.status_code == 200

        # Issue Invoice
        issue_res = await ac.post(
            f"/api/invoices/{invoice_id}/issue",
            json={"invoice_number": "INV-2026-001", "issued_at": datetime.utcnow().isoformat()},
            headers=headers
        )
        assert issue_res.status_code == 200

        # 3. Create Payment Intent linked to Invoice
        intent_id = str(uuid4())
        intent_payload = {
            "payment_intent_id": intent_id,
            "invoice_id": invoice_id,
            "payment_reference": "REF-RECON-E2E",
            "requested_amount": "5000.00",
            "requested_asset": {
                "code": "USDC",
                "decimals": 6,
                "type": "DIGITAL_ASSET"
            },
            "destination": {
                "type": "BLOCKCHAIN_ADDRESS",
                "identifier": "0xMerchantAddress",
                "network": "BASE"
            },
            "payment_method": "DIGITAL_ASSET",
            "expires_at": (datetime.utcnow() + timedelta(days=2)).isoformat()
        }
        intent_res = await ac.post("/api/payment-intents", json=intent_payload, headers=headers)
        assert intent_res.status_code == 201
        intent_id = intent_res.json()["id"]

        # Activate Intent
        act_res = await ac.post(f"/api/payment-intents/{intent_id}/activate", headers=headers)
        assert act_res.status_code == 200

        # 4. Ingest Observed Payment Log
        payment_id = str(uuid4())
        detect_payload = {
            "payment_id": payment_id,
            "payment_intent_id": intent_id,
            "observed_amount": "5000.00",
            "asset": {
                "code": "USDC",
                "decimals": 6,
                "type": "DIGITAL_ASSET"
            },
            "network_code": "BASE",
            "network_type": "BLOCKCHAIN",
            "transaction_reference": "0xTxHashReconE2E",
            "sender": "0xSenderAddress",
            "receiver": "0xMerchantAddress",
            "observed_at": datetime.utcnow().isoformat(),
            "organization_id": org_id
        }
        internal_secret = container.settings.internal_secret
        detect_res = await ac.post(
            "/internal/payments/detect",
            json=detect_payload,
            headers={"X-Internal-Secret": internal_secret}
        )
        assert detect_res.status_code == 201

        # 5. Start Verification (which transitions to CONFIRMED since confirmations = 50 >= 3)
        verify_res = await ac.post(
            f"/internal/payments/{payment_id}/verify",
            headers={"X-Internal-Secret": internal_secret}
        )
        assert verify_res.status_code == 200

        # Get Payment Status (should be CONFIRMED and generate outbox event)
        get_pay_res = await ac.get(f"/api/payments/{payment_id}", headers=headers)
        assert get_pay_res.json()["status"] == "CONFIRMED"
        assert get_pay_res.json()["reconciliation_status"] == "UNRECONCILED"

        # 6. Run Reconciliation Worker (Asynchronous workflow execution)
        worker = ReconciliationWorker()
        processed = await worker.process_pending_events()
        assert processed == 1

        # 7. Query APIs to verify balance, allocations list, and reconciliation status
        # Payment reconciliation status should now be FULLY_ALLOCATED
        get_pay_res = await ac.get(f"/api/payments/{payment_id}", headers=headers)
        assert get_pay_res.json()["reconciliation_status"] == "FULLY_ALLOCATED"

        # Query Invoice Balance
        bal_res = await ac.get(f"/api/invoices/{invoice_id}/balance", headers=headers)
        assert bal_res.status_code == 200
        assert bal_res.json()["total_amount"] == "5000.00"
        assert bal_res.json()["allocated_amount"] == "5000.00"
        assert bal_res.json()["outstanding_amount"] == "0.00"

        # Query Invoice Allocations
        inv_pays_res = await ac.get(f"/api/invoices/{invoice_id}/payments", headers=headers)
        assert inv_pays_res.status_code == 200
        assert len(inv_pays_res.json()) == 1
        assert inv_pays_res.json()[0]["allocated_amount"] == "5000.00"
        assert inv_pays_res.json()[0]["payment_id"] == payment_id

        # Query Payment Allocations list
        pay_allocs_res = await ac.get(f"/api/payments/{payment_id}/allocations", headers=headers)
        assert pay_allocs_res.status_code == 200
        assert len(pay_allocs_res.json()) == 1
        assert pay_allocs_res.json()[0]["allocated_amount"] == "5000.00"
        assert pay_allocs_res.json()[0]["invoice_id"] == invoice_id
