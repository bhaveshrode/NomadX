import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from nomadx.apps.api.main import app


@pytest.mark.asyncio
async def test_payment_api_contract_and_operations():
    # Register Fake Blockchain Client in composition root for test coverage
    from nomadx.src.bootstrap.container import container
    internal_secret = container.settings.internal_secret
    from nomadx.src.infrastructure.blockchain.clients.fake import FakeBlockchainClient
    fake_client = FakeBlockchainClient(current_height=1000)
    fake_client.add_simulated_transfer_log(
        block_number=950,
        tx_hash="0xTxHash123",
        token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", # USDC
        sender="0xSenderAddress",
        receiver="0xMerchantAAddress",
        raw_amount=500000000 # 500 USDC
    )
    container.blockchain_provider.register_client("BASE", fake_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Onboard Merchant A
        reg_a = await ac.post("/auth/register", json={
            "email": "payment_merchant_a@nomadx.com",
            "password": "strongpassword123",
            "name": "Merchant A",
            "organization_name": "Org A Corp",
            "organization_slug": "pay-org-a-corp",
            "default_currency": "USD"
        })
        assert reg_a.status_code == 201
        
        login_a = await ac.post("/auth/login", json={
            "email": "payment_merchant_a@nomadx.com",
            "password": "strongpassword123"
        })
        token_a = login_a.json()["token"]
        
        ctx_res_a = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_a}"})
        org_a_id = ctx_res_a.json()["organization_id"]

        # 2. Onboard Merchant B
        reg_b = await ac.post("/auth/register", json={
            "email": "payment_merchant_b@nomadx.com",
            "password": "strongpassword123",
            "name": "Merchant B",
            "organization_name": "Org B Corp",
            "organization_slug": "pay-org-b-corp",
            "default_currency": "EUR"
        })
        assert reg_b.status_code == 201
        
        login_b = await ac.post("/auth/login", json={
            "email": "payment_merchant_b@nomadx.com",
            "password": "strongpassword123"
        })
        token_b = login_b.json()["token"]
        
        ctx_res_b = await ac.get("/api/secure-context", headers={"Authorization": f"Bearer {token_b}"})
        org_b_id = ctx_res_b.json()["organization_id"]

        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Organization-ID": org_a_id
        }
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Organization-ID": org_b_id
        }

        # 3. Create Invoice for Org A
        inv_id_a = str(uuid4())
        create_invoice_res = await ac.post("/api/invoices", json={
            "invoice_id": inv_id_a,
            "origin_type": "NATIVE",
            "currency_code": "USD",
            "currency_type": "FIAT",
            "payment_terms_type": "NET_30",
            "customer_id": str(uuid4()),
            "billing_snapshot": {
                "legal_name": "Buyer A",
                "email": "buyer@a.com",
                "address": "100 Broadway",
                "country": "US",
                "tax_identifier": "TAX-A"
            }
        }, headers=headers_a)
        assert create_invoice_res.status_code == 201

        # 4. Create PaymentIntent A for Org A
        intent_id_a = str(uuid4())
        expiry_time = (datetime.utcnow() + timedelta(days=2)).isoformat()
        intent_payload = {
            "payment_intent_id": intent_id_a,
            "invoice_id": inv_id_a,
            "payment_reference": "PAY-INT-API-001",
            "requested_amount": "500.00",
            "requested_asset": {
                "code": "USDC",
                "decimals": 6,
                "type": "DIGITAL_ASSET"
            },
            "destination": {
                "type": "BLOCKCHAIN_ADDRESS",
                "identifier": "0xMerchantAAddress",
                "network": "BASE"
            },
            "payment_method": "DIGITAL_ASSET",
            "expires_at": expiry_time
        }
        
        # Successful creation by Org A
        create_intent_res = await ac.post("/api/payment-intents", json=intent_payload, headers=headers_a)
        assert create_intent_res.status_code == 201
        
        intent_data = create_intent_res.json()
        assert intent_data["id"] == intent_id_a
        assert intent_data["requested_amount"] == "500.00"
        assert intent_data["status"] == "CREATED"

        # Attempted creation by Org B on Org A's invoice (should return 404/ownership mismatch)
        create_intent_res_b = await ac.post("/api/payment-intents", json={
            **intent_payload,
            "payment_intent_id": str(uuid4()),
            "payment_reference": "PAY-INT-API-002"
        }, headers=headers_b)
        assert create_intent_res_b.status_code == 404

        # 5. Retrieve PaymentIntent
        # Org A retrieve (success)
        get_intent_res = await ac.get(f"/api/payment-intents/{intent_id_a}", headers=headers_a)
        assert get_intent_res.status_code == 200
        assert get_intent_res.json()["requested_amount"] == "500.00"

        # Org B retrieve Org A's intent (should be 404 to avoid tenant leaks)
        get_intent_res_b = await ac.get(f"/api/payment-intents/{intent_id_a}", headers=headers_b)
        assert get_intent_res_b.status_code == 404

        # 6. List Payment Intents
        list_intents_res_a = await ac.get("/api/payment-intents", headers=headers_a)
        assert list_intents_res_a.status_code == 200
        assert len(list_intents_res_a.json()) == 1

        list_intents_res_b = await ac.get("/api/payment-intents", headers=headers_b)
        assert list_intents_res_b.status_code == 200
        assert len(list_intents_res_b.json()) == 0

        # 7. Activate Intent
        act_res = await ac.post(f"/api/payment-intents/{intent_id_a}/activate", headers=headers_a)
        assert act_res.status_code == 200

        get_intent_res = await ac.get(f"/api/payment-intents/{intent_id_a}", headers=headers_a)
        assert get_intent_res.json()["status"] == "ACTIVE"

        # Org B activation of Org A's intent (should be 404)
        act_res_b = await ac.post(f"/api/payment-intents/{intent_id_a}/activate", headers=headers_b)
        assert act_res_b.status_code == 404

        # 8. Cancel Intent (Will skip cancel for now, so we can detect payment against active intent)

        # 9. Internal Ingestion/Detection API
        payment_id_a = str(uuid4())
        detect_payload = {
            "payment_id": payment_id_a,
            "payment_intent_id": intent_id_a,
            "observed_amount": "500.00",
            "asset": {
                "code": "USDC",
                "decimals": 6,
                "type": "DIGITAL_ASSET"
            },
            "network_code": "BASE",
            "network_type": "BLOCKCHAIN",
            "transaction_reference": "0xTxHash123",
            "sender": "0xSenderAddress",
            "receiver": "0xMerchantAAddress",
            "observed_at": datetime.utcnow().isoformat(),
            "organization_id": org_a_id
        }

        # Call without internal auth secret (should be 401)
        detect_res_no_auth = await ac.post("/internal/payments/detect", json=detect_payload)
        assert detect_res_no_auth.status_code == 401

        # Call with wrong internal auth secret (should be 401)
        detect_res_wrong_auth = await ac.post(
            "/internal/payments/detect", 
            json=detect_payload,
            headers={"X-Internal-Secret": "wrong-secret"}
        )
        assert detect_res_wrong_auth.status_code == 401

        # Successful detection
        detect_res_ok = await ac.post(
            "/internal/payments/detect",
            json=detect_payload,
            headers={"X-Internal-Secret": internal_secret}
        )
        assert detect_res_ok.status_code == 201
        assert detect_res_ok.json()["observed_amount"] == "500.00"
        assert detect_res_ok.json()["status"] == "DETECTED"

        # Duplicate detection (should return 409 Conflict)
        detect_res_dup = await ac.post(
            "/internal/payments/detect",
            json=detect_payload,
            headers={"X-Internal-Secret": internal_secret}
        )
        assert detect_res_dup.status_code == 409

        # 10. Retrieve observed payment
        get_pay_res = await ac.get(f"/api/payments/{payment_id_a}", headers=headers_a)
        assert get_pay_res.status_code == 200
        assert get_pay_res.json()["observed_amount"] == "500.00"

        # Cross tenant lookup (404)
        get_pay_res_b = await ac.get(f"/api/payments/{payment_id_a}", headers=headers_b)
        assert get_pay_res_b.status_code == 404

        # 11. List payments
        list_pay_res_a = await ac.get("/api/payments", headers=headers_a)
        assert list_pay_res_a.status_code == 200
        assert len(list_pay_res_a.json()) == 1

        list_pay_res_b = await ac.get("/api/payments", headers=headers_b)
        assert list_pay_res_b.status_code == 200
        assert len(list_pay_res_b.json()) == 0

        # 12. Verification & Lifecycle Status
        # Start verification first with block_number 999 (confirmations = 2 < 3)
        fake_client.add_simulated_receipt("0xTxHash123", status="0x1", block_number=999)
        
        verify_res_pending = await ac.post(
            f"/internal/payments/{payment_id_a}/verify",
            headers={"X-Internal-Secret": internal_secret}
        )
        # Should return 400 validation error (insufficient confirmations)
        assert verify_res_pending.status_code == 400
        assert "Insufficient block confirmations" in verify_res_pending.json()["detail"]

        get_pay_res = await ac.get(f"/api/payments/{payment_id_a}", headers=headers_a)
        assert get_pay_res.json()["status"] == "PENDING_VERIFICATION"

        # Advance block height to 1005 (confirmations = 7 >= 3)
        fake_client.set_block_height(1005)

        verify_res_ok = await ac.post(
            f"/internal/payments/{payment_id_a}/verify",
            headers={"X-Internal-Secret": internal_secret}
        )
        assert verify_res_ok.status_code == 200

        get_pay_res = await ac.get(f"/api/payments/{payment_id_a}", headers=headers_a)
        assert get_pay_res.json()["status"] == "CONFIRMED"
