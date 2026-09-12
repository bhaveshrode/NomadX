import pytest
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    TelemetryEventDbModel,
    AnomalyDbModel,
    RemediationActionDbModel,
    ReceivableDbModel,
    PaymentAllocationDbModel,
    InvoiceDbModel
)
from nomadx.src.domain.organization.value_objects import OrganizationId
from nomadx.src.application.receivable.commands import AllocatePaymentCommand
from nomadx.src.presentation.api.dependencies.context import ExecutionContext

@pytest.mark.asyncio
async def test_database_outage_and_recovery():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_dbfail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner DbFail",
            "organization_name": "Org DbFail",
            "organization_slug": f"org-dbfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]

        # 1. Evaluate health directly via query service while DB is healthy
        async with container.observability_query_service() as query_service:
            report_healthy = await query_service.evaluate_health_and_readiness(OrganizationId(org_id))
            assert report_healthy["status"] in ("HEALTHY", "DEGRADED", "INACTIVE")

            # 2. Inject DB Outage on this service instance session execute only
            with patch.object(query_service._session, "execute", side_effect=Exception("Mocked DB connection outage")):
                report_fail = await query_service.evaluate_health_and_readiness(OrganizationId(org_id))
                assert report_fail["status"] == "UNHEALTHY"
                assert "database is unreachable" in report_fail["evidence"]
                assert report_fail["readiness"]["status"] == "NOT_READY"
                assert "Platform is unhealthy." in report_fail["readiness"]["barriers"]
                assert report_fail["components"]["database"]["status"] == "UNHEALTHY"

            # 3. DB Recovered: check again
            report_recovered = await query_service.evaluate_health_and_readiness(OrganizationId(org_id))
            assert report_recovered["status"] in ("HEALTHY", "DEGRADED", "INACTIVE")


@pytest.mark.asyncio
async def test_worker_failure_remediation_and_escalation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_wfail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner WFail",
            "organization_name": "Org WFail",
            "organization_slug": f"org-wfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Seed worker failure events to trigger failure burst
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Critical processor crash",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # 2. Run anomaly detection -> anomaly created
        await ac.post("/api/observability/anomalies/detect", headers=headers)

        res_anom = await ac.get("/api/observability/anomalies", headers=headers)
        anomalies = res_anom.json()
        assert len(anomalies) == 1
        assert anomalies[0]["anomaly_type"] == "WORKER_FAILURE_BURST"
        assert anomalies[0]["status"] == "DETECTED"
        assert anomalies[0]["priority"] == "HIGH"
        anomaly_id = anomalies[0]["id"]

        # 3. Intercept health reports to keep ar_event_processor DEGRADED, simulating recovery failures
        async with container.observability_query_service() as query_service:
            original_evaluate = query_service.evaluate_health_and_readiness

            async def mock_evaluate(organization_id):
                report = await original_evaluate(organization_id)
                # Keep ar_event_processor DEGRADED to force verification failure
                report["components"]["ar_event_processor"]["status"] = "DEGRADED"
                return report

            # Patch evaluate_health_and_readiness
            query_service.evaluate_health_and_readiness = mock_evaluate

            # Mock telemetry_service to share query_service session and avoid SQLite deadlock
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def mock_telemetry_service():
                from nomadx.src.application.observability.telemetry_service import TelemetryService
                yield TelemetryService(query_service._session)

            with patch.object(container, "telemetry_service", side_effect=mock_telemetry_service):
                # Attempt 1: Executing remediation -> VERIFICATION_FAILED
                await query_service.run_remediation_pipeline(OrganizationId(org_id))

                # Attempt 2: Executing remediation -> VERIFICATION_FAILED
                await query_service.run_remediation_pipeline(OrganizationId(org_id))

                # Attempt 3: Executing remediation -> VERIFICATION_FAILED
                await query_service.run_remediation_pipeline(OrganizationId(org_id))

                # Attempt 4: Exceeds budget -> Escalated to CRITICAL
                await query_service.run_remediation_pipeline(OrganizationId(org_id))

        # Verify budget exceeded and anomaly escalated (outside of outer session to release lock)
        async with session_manager.session() as session:
            db_anom = (await session.execute(
                select(AnomalyDbModel).where(AnomalyDbModel.id == anomaly_id)
            )).scalar_one()
            assert db_anom.escalated is True
            assert db_anom.priority == "CRITICAL"
            assert db_anom.status == "ACKNOWLEDGED"
            evidence = json.loads(db_anom.evidence)
            assert "retry budget exceeded" in evidence.get("escalation_notes", "")


@pytest.mark.asyncio
async def test_financial_allocation_failure_transaction_rollback():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_allocfail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner AllocFail",
            "organization_name": "Org AllocFail",
            "organization_slug": f"org-allocfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]

        # Seed Invoice & Receivable
        inv_id = str(uuid4())
        rec_id = str(uuid4())
        async with session_manager.session() as session:
            inv = InvoiceDbModel(
                id=inv_id,
                organization_id=org_id,
                invoice_number=f"INV-{uuid4().hex[:6].upper()}",
                origin_type="NATIVE",
                currency_code="USDC",
                currency_type="DIGITAL_ASSET",
                status="ISSUED",
                subtotal="1000.00",
                discount_total="0.00",
                charge_total="0.00",
                tax_total="0.00",
                grand_total="1000.00",
                payment_terms_type="DUE_ON_RECEIPT",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            rec = ReceivableDbModel(
                id=rec_id,
                organization_id=org_id,
                customer_id=str(uuid4()),
                invoice_id=inv_id,
                reference=f"AR-{uuid4().hex[:6].upper()}",
                customer_reference="CUST-3",
                invoice_reference="INV-3",
                currency="USDC",
                original_amount="1000.00",
                allocated_amount="0.00",
                outstanding_amount="1000.00",
                due_date=datetime.utcnow() + timedelta(days=30),
                status="OPEN",
                aging_bucket="CURRENT",
                dunning_stage="NONE",
                idempotency_key="idem-allocfail",
                paid_amount="0.00",
                payment_status="UNPAID",
                collection_status="CURRENT",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(inv)
            session.add(rec)
            await session.commit()

        # Seed the mock payment allocation record
        alloc_ref = str(uuid4())
        async with session_manager.session() as session:
            from nomadx.src.infrastructure.database.models import PaymentAllocationDbModel
            # Create a mock payment allocation
            pa = PaymentAllocationDbModel(
                id=alloc_ref,
                payment_id=str(uuid4()),
                invoice_id=inv_id,
                organization_id=org_id,
                allocated_amount="500.00",
                currency="USDC",
                status="ALLOCATED",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(pa)
            await session.commit()

        # Try to allocate payment, but inject save failure on repo to trigger transaction rollback
        cmd = AllocatePaymentCommand(
            organization_id=org_id,
            receivable_id=rec_id,
            payment_id=str(uuid4()),
            allocated_amount=Decimal("500.00"),
            currency_code="USDC",
            allocation_reference=alloc_ref
        )

        ctx = ExecutionContext(
            user_id="user-1",
            organization_id=org_id,
            role="ADMIN"
        )

        async with container.allocate_payment_handler() as handler:
            # Mock the handler repository save method to raise exception
            with patch.object(handler._allocation_repo, "save", side_effect=Exception("Database crash during save")):
                with pytest.raises(Exception, match="Database crash during save"):
                    await handler.handle(cmd, ctx)

        # Assert no allocations were marked applied and outstanding amount remains exactly 1000.00 (clean rollback)
        async with session_manager.session() as session:
            db_rec = (await session.execute(
                select(ReceivableDbModel).where(ReceivableDbModel.id == rec_id)
            )).scalar_one()
            assert Decimal(db_rec.allocated_amount) == Decimal("0.00")
            assert Decimal(db_rec.outstanding_amount) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_closed_loop_tick_midway_failure():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_tickfail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner TickFail",
            "organization_name": "Org TickFail",
            "organization_slug": f"org-tickfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]

        # Trigger tick directly via query service, but mock evaluate_operational_situations to raise exception midway
        async with container.observability_query_service() as query_service:
            with patch.object(query_service, "evaluate_operational_situations", side_effect=Exception("Crash midway")):
                with pytest.raises(Exception, match="Crash midway"):
                    await query_service.trigger_intelligence_tick(OrganizationId(org_id), actor_id="user-1")


@pytest.mark.asyncio
async def test_api_database_unavailable_and_recovery():
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_db_api_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner DbApi",
            "organization_name": "Org DbApi",
            "organization_slug": f"org-dbapi-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Normal state: healthy
        res_healthy = await ac.get("/api/observability/health", headers=headers)
        assert res_healthy.status_code == 200
        assert res_healthy.json()["status"] in ("HEALTHY", "DEGRADED", "INACTIVE")

        # 2. Inject DB Outage globally on AsyncSession.execute (except for authentication membership queries)
        from sqlalchemy.ext.asyncio import AsyncSession
        original_execute = AsyncSession.execute

        async def mock_execute(self_session, statement, *args, **kwargs):
            stmt_str = str(statement)
            if "membership" in stmt_str.lower():
                return await original_execute(self_session, statement, *args, **kwargs)
            raise Exception("Mocked DB connection outage")

        with patch.object(AsyncSession, "execute", new=mock_execute):
            # API request should fail with controlled 500 error
            res_rec = await ac.get("/api/receivables", headers=headers)
            assert res_rec.status_code == 500

            # Health degradation check: health route returns status UNHEALTHY and NOT_READY
            res_unhealthy = await ac.get("/api/observability/health", headers=headers)
            assert res_unhealthy.status_code == 200
            report = res_unhealthy.json()
            assert report["status"] == "UNHEALTHY"
            assert "database is unreachable" in report["evidence"]
            assert report["readiness"]["status"] == "NOT_READY"
            assert "Platform is unhealthy." in report["readiness"]["barriers"]

        # 3. Database restored (patch removed): health recovers
        res_recovered = await ac.get("/api/observability/health", headers=headers)
        assert res_recovered.status_code == 200
        assert res_recovered.json()["status"] in ("HEALTHY", "DEGRADED", "INACTIVE")


@pytest.mark.asyncio
async def test_closed_loop_worker_failure_remediation_recovery():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_cl_worker_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner ClWorker",
            "organization_name": "Org ClWorker",
            "organization_slug": f"org-clworker-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Seed worker failure events to telemetry service
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Unexpected crash",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # 2. Trigger closed-loop intelligence tick via API
        res_tick = await ac.post("/api/observability/intelligence/tick", headers=headers)
        assert res_tick.status_code == 200
        tick_data = res_tick.json()
        assert tick_data["status"] == "SUCCESS"
        assert tick_data["anomalies_detected"] == 1
        assert tick_data["remediations_executed"] == 1
        assert tick_data["final_health"] == "HEALTHY"

        # 3. Verify anomaly resolved
        res_anom = await ac.get("/api/observability/anomalies", headers=headers)
        anoms = res_anom.json()
        assert len(anoms) == 1
        assert anoms[0]["status"] == "RESOLVED"
        assert "automated worker recovery" in anoms[0]["resolution_notes"]

        # 4. Verify remediation history shows success
        res_rem = await ac.get("/api/observability/remediations", headers=headers)
        remediations = res_rem.json()
        assert len(remediations) == 1
        assert remediations[0]["status"] == "SUCCESS"
        assert remediations[0]["action_type"] == "RECOVER_WORKER"


async def _setup_confirmed_payment(org_id: str, payment_id: str, amount: Decimal = Decimal("5000.00"), status: str = "CONFIRMED") -> None:
    from nomadx.src.infrastructure.database.repositories import PostgresPaymentRepository
    from nomadx.src.domain.payment import PaymentId, PaymentAsset, PaymentNetwork, Payment
    from nomadx.src.domain.shared.clock.clock import Clock
    from nomadx.src.domain.organization.value_objects import OrganizationId
    from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money

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


@pytest.mark.asyncio
async def test_settlement_failure_transaction_rollback():
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_set_fail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner SetFail",
            "organization_name": "Org SetFail",
            "organization_slug": f"org-setfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Setup Confirmed Payment
        pay_id = str(uuid4())
        await _setup_confirmed_payment(org_id, pay_id, Decimal("5000.00"))

        # 1. Create settlement -> PENDING
        res_create = await ac.post("/api/settlements", json={
            "payment_id": pay_id,
            "amount": "2000.00",
            "asset": "USDC",
            "network": "BASE",
            "destination": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "method": "DIGITAL_ASSET",
            "reference": f"SET-FAIL-{uuid4().hex[:6]}",
            "idempotency_key": f"idem-setfail-{uuid4().hex[:6]}"
        }, headers=headers)
        assert res_create.status_code == 201
        sid = res_create.json()["id"]

        # 2. Try to process the settlement, but patch PostgresSettlementRepository.save to raise an exception
        from nomadx.src.infrastructure.database.repositories import PostgresSettlementRepository
        from nomadx.src.domain.settlement.value_objects import SettlementId
        with patch.object(PostgresSettlementRepository, "save", side_effect=Exception("Database crash during settlement save")):
            res_proc = await ac.post(f"/api/settlements/{sid}/process", headers=headers)
            assert res_proc.status_code == 500

        # 3. Assert status is still PENDING (rolled back cleanly)
        async with session_manager.session() as session:
            repo = PostgresSettlementRepository(session)
            db_s = await repo.get_by_id(SettlementId(sid))
            assert db_s.status.value == "PENDING"


@pytest.mark.asyncio
async def test_reconciliation_failure_transaction_rollback():
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_rec_fail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner RecFail",
            "organization_name": "Org RecFail",
            "organization_slug": f"org-recfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Create settlement reconciliation -> DRAFT
        ref = f"SET-REC-FAIL-{uuid4().hex[:6].upper()}"
        res_create = await ac.post("/api/settlement-reconciliations", json={
            "reference": ref,
            "merchant_reference": "MERCH-FAIL",
            "start_date": "2026-09-01T00:00:00Z",
            "end_date": "2026-09-05T00:00:00Z",
            "currency_code": "USDC",
            "idempotency_key": f"idem-recfail-{uuid4().hex[:8]}"
        }, headers=headers)
        assert res_create.status_code == 201
        rec_id = res_create.json()["id"]

        # 2. Try to open the reconciliation, but patch PostgresSettlementRepository in settlement_reconciliation_repository to raise exception on save
        from nomadx.src.infrastructure.database.settlement_reconciliation_repository import PostgresSettlementRepository as PostgresSettlementReconciliationRepository
        from nomadx.src.domain.settlement_reconciliation.value_objects import SettlementId
        with patch.object(PostgresSettlementReconciliationRepository, "save", side_effect=Exception("Database crash during reconciliation save")):
            res_open = await ac.post(f"/api/settlement-reconciliations/{rec_id}/open", headers=headers)
            assert res_open.status_code == 500

        # 3. Assert status is still DRAFT (rolled back cleanly)
        async with session_manager.session() as session:
            repo = PostgresSettlementReconciliationRepository(session)
            db_rec = await repo.get_by_id(SettlementId(rec_id))
            assert db_rec.status.value == "DRAFT"


@pytest.mark.asyncio
async def test_consistency_check_failure():
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_const_fail_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner ConstFail",
            "organization_name": "Org ConstFail",
            "organization_slug": f"org-constfail-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        login_res = await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })
        token = login_res.json()["token"]
        user_id = login_res.json()["user"]["id"]
        org_id = reg.json()["organization"]["id"]

        # Promote user to PLATFORM_ADMIN in DB to authorize run
        async with session_manager.session() as db_session:
            from nomadx.src.infrastructure.database.models import MembershipDbModel
            from sqlalchemy import update
            stmt = (
                update(MembershipDbModel)
                .where(MembershipDbModel.user_id == user_id)
                .values(role="PLATFORM_ADMIN")
            )
            await db_session.execute(stmt)
            await db_session.commit()

        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Patch the worker's run_integrity_sweep to raise an exception
        from nomadx.src.infrastructure.workers.financial_consistency_automation_worker import FinancialConsistencyAutomationWorker
        with patch.object(FinancialConsistencyAutomationWorker, "run_integrity_sweep", side_effect=Exception("Consistency sweep error")):
            res = await ac.post("/api/financial-consistency/automation/run", headers=headers)
            assert res.status_code == 500


@pytest.mark.asyncio
async def test_remediation_execution_failure_escalation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_rem_esc_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner RemEsc",
            "organization_name": "Org RemEsc",
            "organization_slug": f"org-remesc-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]

        # 1. Seed worker failure events to trigger anomaly
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Unexpected crash",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # 2. Query service detection and remediation
        async with container.observability_query_service() as query_service:
            # Detect anomaly first
            await query_service.run_anomaly_detection(OrganizationId(org_id))

            # Mock telemetry_service.record_event to crash ONLY on worker recovery operations
            from nomadx.src.application.observability.telemetry_service import TelemetryService
            original_record = TelemetryService.record_event

            async def mock_record_event(self_svc, component, operation, message, severity, status, organization_id, metadata=None):
                if operation == "remediation_recovery":
                    raise Exception("Mocked telemetry crash during recovery")
                return await original_record(self_svc, component, operation, message, severity, status, organization_id, metadata)

            with patch.object(TelemetryService, "record_event", new=mock_record_event):
                # Run remediation pipeline 4 times
                # 1st attempt: fails
                res1 = await query_service.run_remediation_pipeline(OrganizationId(org_id))
                assert res1["executed_remediations_count"] == 1
                
                # 2nd attempt: fails
                res2 = await query_service.run_remediation_pipeline(OrganizationId(org_id))
                assert res2["executed_remediations_count"] == 1

                # 3rd attempt: fails
                res3 = await query_service.run_remediation_pipeline(OrganizationId(org_id))
                assert res3["executed_remediations_count"] == 1

                # 4th run: detects retry budget exceeded, escalates, and skips execution
                res4 = await query_service.run_remediation_pipeline(OrganizationId(org_id))
                assert res4["executed_remediations_count"] == 0

        # 3. Assert the anomaly has been escalated to CRITICAL / ACKNOWLEDGED due to retry budget exhaustion
        async with session_manager.session() as session:
            from nomadx.src.infrastructure.database.models import AnomalyDbModel
            anomaly = (await session.execute(
                select(AnomalyDbModel).where(AnomalyDbModel.organization_id == org_id)
            )).scalars().all()[0]
            assert anomaly.escalated is True
            assert anomaly.priority == "CRITICAL"
            assert anomaly.status == "ACKNOWLEDGED"
            evidence = json.loads(anomaly.evidence)
            assert "retry budget exceeded" in evidence["escalation_notes"]


@pytest.mark.asyncio
async def test_closed_loop_persistent_failure_circuit_breaker():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"owner_cl_cb_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner ClCb",
            "organization_name": "Org ClCb",
            "organization_slug": f"org-clcb-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # 1. Seed worker failure events
        async with container.telemetry_service() as telemetry_service:
            for _ in range(3):
                await telemetry_service.record_event(
                    component="WORKER",
                    operation="process_payment_events",
                    message="Unexpected crash",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )

        # 2. Mock TelemetryService.record_event to crash on worker recovery
        from nomadx.src.application.observability.telemetry_service import TelemetryService
        original_record = TelemetryService.record_event

        async def mock_record_event(self_svc, component, operation, message, severity, status, organization_id, metadata=None):
            if operation == "remediation_recovery":
                raise Exception("Mocked telemetry crash during recovery")
            return await original_record(self_svc, component, operation, message, severity, status, organization_id, metadata)

        with patch.object(TelemetryService, "record_event", new=mock_record_event):
            # Call intelligence tick 4 times
            # Tick 1: detects anomaly, runs remediation (attempt 1) and fails
            res1 = await ac.post("/api/observability/intelligence/tick", headers=headers)
            assert res1.status_code == 200
            assert res1.json()["anomalies_detected"] == 1
            assert res1.json()["remediations_executed"] == 1

            # Tick 2: correlates anomaly, runs remediation (attempt 2) and fails
            res2 = await ac.post("/api/observability/intelligence/tick", headers=headers)
            assert res2.status_code == 200
            assert res2.json()["anomalies_detected"] == 1
            assert res2.json()["remediations_executed"] == 1

            # Tick 3: correlates anomaly, runs remediation (attempt 3) and fails
            res3 = await ac.post("/api/observability/intelligence/tick", headers=headers)
            assert res3.status_code == 200
            assert res3.json()["anomalies_detected"] == 1
            assert res3.json()["remediations_executed"] == 1

            # Tick 4: correlates anomaly, detects retry budget exceeded, escalates, and skips execution
            res4 = await ac.post("/api/observability/intelligence/tick", headers=headers)
            assert res4.status_code == 200
            assert res4.json()["anomalies_detected"] == 1
            assert res4.json()["remediations_executed"] == 0

        # 3. Verify escalated status in database
        async with session_manager.session() as session:
            from nomadx.src.infrastructure.database.models import AnomalyDbModel
            anomaly = (await session.execute(
                select(AnomalyDbModel).where(AnomalyDbModel.organization_id == org_id)
            )).scalars().all()[0]
            assert anomaly.escalated is True
            assert anomaly.priority == "CRITICAL"
            assert anomaly.status == "ACKNOWLEDGED"
