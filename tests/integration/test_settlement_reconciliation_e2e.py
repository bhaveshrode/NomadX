import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import json
from sqlalchemy import select

from nomadx.apps.api.main import app
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import SettlementReconciliationDbModel, PaymentDbModel, OutboxEventDbModel
from nomadx.src.bootstrap.container import container
from nomadx.src.domain.settlement_reconciliation.value_objects import SettlementId
from nomadx.src.infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository


async def _setup_confirmed_payment_db(org_id: str, tx_ref: str, amount: Decimal, observed_at: datetime) -> None:
    async with session_manager.session() as session:
        payment = PaymentDbModel(
            id=str(uuid4()),
            organization_id=org_id,
            observed_amount=str(amount),
            asset_code="USDC",
            asset_type="DIGITAL_ASSET",
            asset_decimals=6,
            network="BASE",
            transaction_reference=tx_ref,
            sender="0xSenderWalletE2E",
            receiver="0xReceiverWalletE2E",
            status="CONFIRMED",
            reconciliation_status="UNRECONCILED",
            observed_at=observed_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(payment)
        await session.commit()


@pytest.mark.asyncio
async def test_settlement_reconciliation_e2e_full_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Register & login Org A (Admin/Owner) and Org A (Member)
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_a_e2e@example.com",
            "password": "strongpassword123",
            "name": "Owner A",
            "organization_name": "Org A E2E",
            "organization_slug": f"org-a-e2e-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a_owner = (await ac.post("/auth/login", json={"email": "owner_a_e2e@example.com", "password": "strongpassword123"})).json()["token"]
        org_a_id = reg_a.json()["organization"]["id"]
        headers_a_owner = {"Authorization": f"Bearer {token_a_owner}", "X-Organization-ID": org_a_id}

        # Create Member user for Org A to test RBAC
        await ac.post("/members/invite", json={
            "email": "member_a_e2e@example.com",
            "role": "MEMBER"
        }, headers=headers_a_owner)
        
        # Complete invite / register Member
        await ac.post("/auth/register", json={
            "email": "member_a_e2e@example.com",
            "password": "strongpassword123",
            "name": "Member A",
            "organization_name": "Org A E2E",
            "organization_slug": f"org-a-e2e-member-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a_member = (await ac.post("/auth/login", json={"email": "member_a_e2e@example.com", "password": "strongpassword123"})).json()["token"]
        headers_a_member = {"Authorization": f"Bearer {token_a_member}", "X-Organization-ID": org_a_id}

        # 2. Register & login Org B (Tenant Isolation checks)
        reg_b = await ac.post("/auth/register", json={
            "email": "owner_b_e2e@example.com",
            "password": "strongpassword123",
            "name": "Owner B",
            "organization_name": "Org B E2E",
            "organization_slug": f"org-b-e2e-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b_owner = (await ac.post("/auth/login", json={"email": "owner_b_e2e@example.com", "password": "strongpassword123"})).json()["token"]
        org_b_id = reg_b.json()["organization"]["id"]
        headers_b_owner = {"Authorization": f"Bearer {token_b_owner}", "X-Organization-ID": org_b_id}

        # 3. Simulate PaymentReceived Outbox Event for Org A
        correlation_id = str(uuid4())
        payment_id_1 = str(uuid4())
        tx_ref_1 = f"0xTX-E2E-{uuid4().hex[:6].upper()}"
        
        event_model = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentReceived",
            payload=json.dumps({
                "payment_id": payment_id_1,
                "amount": "1500.00",
                "currency": "USDC",
                "organization_id": org_a_id,
                "transaction_reference": tx_ref_1,
                "merchant_reference": "MERCH-E2E-A",
                "observed_at": "2026-09-02T12:00:00Z",
                "_metadata": {
                    "correlation_id": correlation_id,
                    "causation_id": correlation_id
                }
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        async with session_manager.session() as session:
            session.add(event_model)
            await session.commit()

        # 4. Run Settlement Event Processor -> Auto-creates batch and maps entry
        async with container.settlement_event_processor() as processor:
            processed_evts = await processor.process_financial_events()
            assert processed_evts == 1

        # Retrieve the automatically created settlement ID
        async with session_manager.session() as session:
            stmt = select(SettlementReconciliationDbModel).where(
                SettlementReconciliationDbModel.organization_id == org_a_id,
                SettlementReconciliationDbModel.merchant_reference == "MERCH-AUTO"
            )
            res = await session.execute(stmt)
            settlement_db = res.scalar_one()
            settlement_id = settlement_db.id
            assert settlement_db.currency_code == "USDC"
            assert settlement_db.status == "OPEN"
            assert settlement_db.processing_state == "PENDING"

        # 5. Verify Multi-Tenant Boundary: Org B cannot access Org A's Settlement timeline/detail
        res_detail_b = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers_b_owner)
        assert res_detail_b.status_code == 404

        res_timeline_b = await ac.get(f"/api/settlement-reconciliations/{settlement_id}/timeline", headers=headers_b_owner)
        assert len(res_timeline_b.json()) == 0

        # 6. Verify RBAC behavior: Member of Org A cannot Pause/Resume/Complete
        res_pause_m = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/pause", headers=headers_a_member)
        assert res_pause_m.status_code == 403

        # 7. Pause & Resume Operational Controls (Admin/Owner Org A)
        res_pause = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/pause", headers=headers_a_owner)
        assert res_pause.status_code == 200
        assert res_pause.json()["paused"] is True

        # While paused, worker shouldn't process it. Verify worker poll ignores it.
        async with container.settlement_automation_worker() as worker:
            polled = await worker.process_eligible_settlements()
            assert polled == 0

        res_resume = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/resume", headers=headers_a_owner)
        assert res_resume.status_code == 200
        assert res_resume.json()["paused"] is False

        # 8. Set up matching Payment in Database
        await _setup_confirmed_payment_db(org_a_id, tx_ref_1, Decimal("1500.00"), datetime(2026, 9, 2, 12, 0, 0))

        # 9. Run Settlement Automation Worker to Auto-reconcile
        async with container.settlement_automation_worker() as worker:
            polled = await worker.process_eligible_settlements()
            assert polled == 1

        # Check detail -> status is COMPLETED (auto-completed by automation worker since it is balanced)
        res_detail_a = await ac.get(f"/api/settlement-reconciliations/{settlement_id}", headers=headers_a_owner)
        assert res_detail_a.json()["status"] == "COMPLETED"
        assert res_detail_a.json()["reconciliation_status"] == "MATCHED"
        assert res_detail_a.json()["processing_state"] == "COMPLETED"

        # 10. Finalization Immutability Check: Mutation/Delete throws error
        async with session_manager.session() as session:
            repo = PostgresSettlementRepository(session)
            with pytest.raises(ValueError) as exc_info:
                await repo.delete(SettlementId(settlement_id))
            assert "CANNOT_DELETE_FINALIZED_SETTLEMENT" in str(exc_info.value)

        res_add_entry = await ac.post(f"/api/settlement-reconciliations/{settlement_id}/entries", json={
            "entry_reference": "LINE-E2E-2",
            "source_transaction_reference": "0xTX-NEW",
            "source_context_type": "PAYMENT",
            "gross_amount": 100.00,
            "fees_amount": 0.00,
            "adjustments_amount": 0.00,
            "net_amount": 100.00,
            "currency_code": "USDC"
        }, headers=headers_a_owner)
        assert res_add_entry.status_code == 409  # Conflict: CANNOT_MUTATE_FINALIZED_SETTLEMENT

        # 11. Correlation & Causation Ancestry Verification
        async with session_manager.session() as session:
            stmt = select(OutboxEventDbModel).where(
                OutboxEventDbModel.event_type == "SettlementEntryAdded"
            )
            res_outbox = await session.execute(stmt)
            outbox_event = res_outbox.scalars().first()
            payload = json.loads(outbox_event.payload)
            metadata = payload["_metadata"]
            
            # Lineage trace back to originating payment correlation ID!
            assert metadata["correlation_id"] == correlation_id


@pytest.mark.asyncio
async def test_settlement_reconciliation_e2e_refund_and_reversal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        reg_a = await ac.post("/auth/register", json={
            "email": "owner_refund@example.com",
            "password": "strongpassword123",
            "name": "Owner Refund",
            "organization_name": "Org Refund",
            "organization_slug": f"org-ref-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={"email": "owner_refund@example.com", "password": "strongpassword123"})).json()["token"]
        org_id = reg_a.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_id}

        # 1. Create a Settlement batch
        ref = f"SET-REF-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-REF",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-{uuid4().hex[:8]}"
        }, headers=headers)
        settlement_id = res_create.json()["id"]

        await ac.post(f"/api/settlement-reconciliations/{settlement_id}/open", headers=headers)

        # 2. Simulate PaymentRefunded Event
        tx_ref_refund = f"0xTX-REF-{uuid4().hex[:6].upper()}"
        refund_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentRefunded",
            payload=json.dumps({
                "payment_id": str(uuid4()),
                "amount": "-300.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref_refund,
                "merchant_reference": "MERCH-REF",
                "observed_at": "2026-09-02T14:00:00Z"
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        
        # 3. Simulate PaymentReversed Event
        tx_ref_reversal = f"0xTX-REV-{uuid4().hex[:6].upper()}"
        reversal_event = OutboxEventDbModel(
            id=str(uuid4()),
            event_type="PaymentReversed",
            payload=json.dumps({
                "payment_id": str(uuid4()),
                "amount": "-150.00",
                "currency": "USDC",
                "organization_id": org_id,
                "transaction_reference": tx_ref_reversal,
                "merchant_reference": "MERCH-REF",
                "observed_at": "2026-09-02T15:00:00Z"
            }),
            processed=False,
            status="PENDING",
            created_at=datetime.utcnow()
        )

        async with session_manager.session() as session:
            session.add(refund_event)
            session.add(reversal_event)
            await session.commit()

        # Run processor to ingest
        async with container.settlement_event_processor() as processor:
            processed_count = await processor.process_financial_events()
            assert processed_count == 2

        # Verify entry lines created in active batch
        res_entries = await ac.get(f"/api/settlement-reconciliations/{settlement_id}/entries", headers=headers)
        entries_json = res_entries.json()
        assert len(entries_json) == 2
        
        refund_entry = next(e for e in entries_json if e["source_transaction_reference"] == tx_ref_refund)
        assert refund_entry["gross_amount"] == "-300.00"
        
        reversal_entry = next(e for e in entries_json if e["source_transaction_reference"] == tx_ref_reversal)
        assert reversal_entry["gross_amount"] == "-150.00"
