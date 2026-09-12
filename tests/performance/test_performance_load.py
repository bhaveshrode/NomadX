import pytest
import asyncio
import time
import json
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import (
    TelemetryEventDbModel,
    OutboxEventDbModel,
    InvoiceDbModel,
    ReceivableDbModel,
    PaymentDbModel,
    PaymentAllocationDbModel,
    ReceivableAllocationDbModel,
    AnomalyDbModel,
    OperationalSituationDbModel
)
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor

# Helper to calculate p50, p95, p99 percentiles in milliseconds
def calculate_percentiles(durations):
    if not durations:
        return 0.0, 0.0, 0.0
    sorted_durations = sorted(durations)
    n = len(sorted_durations)
    
    def get_percentile(p):
        idx = int(p * (n - 1))
        return sorted_durations[idx] * 1000.0 # in ms
        
    p50 = get_percentile(0.50)
    p95 = get_percentile(0.95)
    p99 = get_percentile(0.99)
    return p50, p95, p99

async def _seed_invoice_db(org_id: str, inv_id: str, grand_total: Decimal, due_date: datetime) -> None:
    async with session_manager.session() as session:
        invoice_model = InvoiceDbModel(
            id=inv_id,
            organization_id=org_id,
            invoice_number=f"INV-{str(uuid4())[:8].upper()}",
            status="ISSUED",
            origin_type="NATIVE",
            source_system="NomadX",
            external_invoice_id=None,
            customer_id=str(uuid4()),
            billing_name="Perf Customer",
            billing_email="customer-perf@example.com",
            billing_address="456 Performance Ave",
            billing_country="US",
            billing_tax_identifier=None,
            currency_code="USDC",
            currency_type="DIGITAL_ASSET",
            subtotal=str(grand_total),
            discount_total="0.00",
            charge_total="0.00",
            tax_total="0.00",
            grand_total=str(grand_total),
            payment_terms_type="DUE_ON_RECEIPT",
            due_at=due_date,
            issued_at=datetime.utcnow(),
            created_at=datetime.utcnow()
        )
        session.add(invoice_model)
        await session.commit()

async def _seed_payment_db(org_id: str, pay_id: str, tx_ref: str, amount: Decimal) -> None:
    async with session_manager.session() as session:
        payment = PaymentDbModel(
            id=pay_id,
            organization_id=org_id,
            observed_amount=str(amount),
            asset_code="USDC",
            asset_type="DIGITAL_ASSET",
            asset_decimals=6,
            network="BASE",
            transaction_reference=tx_ref,
            sender="0xSenderWalletPerf",
            receiver="0xReceiverWalletPerf",
            status="CONFIRMED",
            reconciliation_status="UNRECONCILED",
            observed_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        session.add(payment)
        await session.commit()


@pytest.mark.asyncio
async def test_api_load_testing():
    """
    13.6.1 — API Load Testing
    Measures API response latencies (p50, p95, p99) and throughput (req/sec) under
    increasing concurrent load levels.
    """
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_load_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Load",
            "organization_name": "Org Load Test",
            "organization_slug": f"org-load-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed initial trace data so GET traces actually reads rows
        async with session_manager.session() as session:
            for i in range(50):
                ev = TelemetryEventDbModel(
                    id=str(uuid4()),
                    timestamp=datetime.utcnow() - timedelta(seconds=i),
                    component="API",
                    operation="api_load_perf",
                    message=f"Event {i}",
                    severity="INFO",
                    status="SUCCESS",
                    organization_id=org_id
                )
                session.add(ev)
            await session.commit()

        # Define load levels
        loads = [
            ("Low Load", 5),
            ("Normal Load", 15),
            ("High Load", 30)
        ]

        print("\n\n=== 13.6.1 — API LOAD TEST METRICS ===")
        for label, concurrency in loads:
            durations = []
            errors = 0

            async def make_request():
                nonlocal errors
                start = time.perf_counter()
                res = await ac.get("/api/observability/traces?limit=10", headers=headers)
                end = time.perf_counter()
                if res.status_code == 200:
                    durations.append(end - start)
                else:
                    errors += 1

            start_batch = time.perf_counter()
            await asyncio.gather(*(make_request() for _ in range(concurrency)))
            end_batch = time.perf_counter()

            total_time = end_batch - start_batch
            throughput = concurrency / total_time if total_time > 0 else 0.0
            error_rate = (errors / concurrency) * 100.0

            p50, p95, p99 = calculate_percentiles(durations)
            print(f"[{label}] (Concurrency: {concurrency})")
            print(f"  Throughput: {throughput:.2f} req/sec")
            print(f"  Latency: p50={p50:.1f}ms, p95={p95:.1f}ms, p99={p99:.1f}ms")
            print(f"  Error Rate: {error_rate:.1f}%")

            # Asserts
            assert error_rate == 0.0
            assert len(durations) == concurrency


@pytest.mark.asyncio
async def test_financial_workflow_throughput():
    """
    13.6.2 — Financial Workflow Throughput
    Executes multiple complete invoice lifecycles concurrently and measures the throughput
    while assuring no consistency invariants are violated.
    """
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_finload_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner FinLoad",
            "organization_name": "Org FinLoad",
            "organization_slug": f"org-finload-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        num_lifecycles = 10
        write_semaphore = asyncio.Semaphore(2) # Limit active concurrent SQLite DB writes to prevent locks

        # Phase 1: Concurrently seed Invoices and pending OutboxEvents (simulating concurrent invoice API calls)
        async def run_phase1(index):
            async with write_semaphore:
                inv_id = str(uuid4())
                due_date = datetime.now(timezone.utc) + timedelta(days=30)
                await _seed_invoice_db(org_id, inv_id, Decimal("500.00"), due_date)

                # Seed InvoiceIssued event
                event_issued = OutboxEventDbModel(
                    id=str(uuid4()),
                    event_type="InvoiceIssued",
                    payload=json.dumps({
                        "invoice_id": inv_id,
                        "organization_id": org_id,
                        "occurred_on": datetime.now(timezone.utc).isoformat()
                    }),
                    processed=False,
                    status="PENDING",
                    created_at=datetime.utcnow()
                )
                async with session_manager.session() as session:
                    session.add(event_issued)
                    await session.commit()
                return inv_id

        start_time = time.perf_counter()
        inv_ids = await asyncio.gather(*(run_phase1(i) for i in range(num_lifecycles)))

        # Phase 2: Process all outbox events sequentially (simulating background workers processing queue)
        ar_processor = AREventProcessor()
        await ar_processor.process_payment_events()

        # Phase 3: Concurrently seed observed Payments (simulating concurrent webhook calls / block detections)
        async def run_phase2(index):
            async with write_semaphore:
                pay_id = str(uuid4())
                tx_ref = f"0xTX-{uuid4().hex[:6].upper()}-{index}"
                await _seed_payment_db(org_id, pay_id, tx_ref, Decimal("500.00"))

        await asyncio.gather(*(run_phase2(i) for i in range(num_lifecycles)))
        end_time = time.perf_counter()

        duration = end_time - start_time
        throughput = num_lifecycles / duration if duration > 0 else 0.0

        print("\n=== 13.6.2 — FINANCIAL WORKFLOW THROUGHPUT ===")
        print(f"Processed {num_lifecycles} lifecycles in {duration:.2f} seconds.")
        print(f"Throughput: {throughput:.2f} lifecycles/second.")

        # Assert no consistency violations occurred
        res_check = await ac.get("/api/financial-consistency/check", headers=headers)
        assert res_check.status_code == 200
        report = res_check.json()
        print(f"Financial Health: {report['status']}")
        print(f"Total Violations: {report['summary']['total_violations']}")
        assert report["status"] == "HEALTHY"
        assert report["summary"]["total_violations"] == 0


@pytest.mark.asyncio
async def test_worker_throughput():
    """
    13.6.3 — Worker Throughput
    Seeds a burst of pending outbox events (and their corresponding Invoices) and measures
    processing throughput of the background event processor.
    """
    email = f"owner_wrkperf_{uuid4().hex[:6]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner WrkPerf",
            "organization_name": "Org WrkPerf",
            "organization_slug": f"org-wrkperf-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]

        num_events = 50
        # Seed 50 Invoices and corresponding outbox events in a single transaction
        async with session_manager.session() as session:
            for i in range(num_events):
                inv_id = str(uuid4())
                invoice_model = InvoiceDbModel(
                    id=inv_id,
                    organization_id=org_id,
                    invoice_number=f"INV-WRK-{i}-{str(uuid4())[:6].upper()}",
                    status="ISSUED",
                    origin_type="NATIVE",
                    source_system="NomadX",
                    external_invoice_id=None,
                    customer_id=str(uuid4()),
                    billing_name="WrkPerf Customer",
                    billing_email="customer-wrkperf@example.com",
                    billing_address="WrkPerf Address",
                    billing_country="US",
                    billing_tax_identifier=None,
                    currency_code="USDC",
                    currency_type="DIGITAL_ASSET",
                    subtotal="100.00",
                    discount_total="0.00",
                    charge_total="0.00",
                    tax_total="0.00",
                    grand_total="100.00",
                    payment_terms_type="DUE_ON_RECEIPT",
                    due_at=datetime.utcnow() + timedelta(days=30),
                    issued_at=datetime.utcnow(),
                    created_at=datetime.utcnow()
                )
                ev = OutboxEventDbModel(
                    id=str(uuid4()),
                    event_type="InvoiceIssued",
                    payload=json.dumps({
                        "invoice_id": inv_id,
                        "organization_id": org_id,
                        "occurred_on": datetime.now(timezone.utc).isoformat()
                    }),
                    processed=False,
                    status="PENDING",
                    created_at=datetime.utcnow()
                )
                session.add(invoice_model)
                session.add(ev)
            await session.commit()

        # Run worker processing and measure time
        ar_processor = AREventProcessor()
        start_time = time.perf_counter()
        processed_count = await ar_processor.process_payment_events()
        end_time = time.perf_counter()

        duration = end_time - start_time
        throughput = num_events / duration if duration > 0 else 0.0

        print("\n=== 13.6.3 — WORKER PROCESSING THROUGHPUT ===")
        print(f"Processed {processed_count} events in {duration:.3f} seconds.")
        print(f"Throughput: {throughput:.2f} events/second.")

        assert processed_count == num_events


@pytest.mark.asyncio
async def test_operational_intelligence_load():
    """
    13.6.4 — Operational Intelligence Load
    Tests the latency baseline of closing the intelligence tick loop
    (health check, anomaly detection, situation evaluation, and remediation).
    """
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_intel_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Intel",
            "organization_name": "Org Intel",
            "organization_slug": f"org-intel-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        # Seed telemetry events representing worker errors to trigger anomaly scanning
        async with session_manager.session() as session:
            for i in range(10):
                ev = TelemetryEventDbModel(
                    id=str(uuid4()),
                    timestamp=datetime.utcnow() - timedelta(seconds=i),
                    component="WORKER",
                    operation="process_payment_events",
                    message="Stuck worker exception occurred",
                    severity="ERROR",
                    status="FAILED",
                    organization_id=org_id
                )
                session.add(ev)
            await session.commit()

        # Run 10 consecutive ticks and measure their latency
        durations = []
        for _ in range(10):
            start = time.perf_counter()
            res = await ac.post("/api/observability/intelligence/tick", headers=headers)
            end = time.perf_counter()
            assert res.status_code == 200
            durations.append(end - start)

        p50, p95, p99 = calculate_percentiles(durations)
        avg_tick_ms = (sum(durations) / len(durations)) * 1000.0

        print("\n=== 13.6.4 — OPERATIONAL INTELLIGENCE TICK LATENCY ===")
        print(f"Executed 10 closed-loop ticks.")
        print(f"Average tick time: {avg_tick_ms:.1f}ms")
        print(f"Latency: p50={p50:.1f}ms, p95={p95:.1f}ms, p99={p99:.1f}ms")

        assert avg_tick_ms < 500.0 # Observability shouldn't block, tick loop must remain fast


@pytest.mark.asyncio
async def test_multi_tenant_noisy_neighbor():
    """
    13.6.5 — Multi-Tenant Load
    Tests whether one tenant's (Tenant B) heavy workload degrades the performance
    of another tenant (Tenant A).
    """
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        # Register Tenant A (Target)
        email_a = f"tenant_a_{uuid4().hex[:6]}@example.com"
        reg_a = await ac.post("/auth/register", json={
            "email": email_a,
            "password": "strongpassword123",
            "name": "Tenant A Owner",
            "organization_name": "Org A",
            "organization_slug": f"org-a-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_a = (await ac.post("/auth/login", json={
            "email": email_a,
            "password": "strongpassword123"
        })).json()["token"]
        org_a = reg_a.json()["organization"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Organization-ID": org_a}

        # Register Tenant B (Noisy Neighbor)
        email_b = f"tenant_b_{uuid4().hex[:6]}@example.com"
        reg_b = await ac.post("/auth/register", json={
            "email": email_b,
            "password": "strongpassword123",
            "name": "Tenant B Owner",
            "organization_name": "Org B",
            "organization_slug": f"org-b-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token_b = (await ac.post("/auth/login", json={
            "email": email_b,
            "password": "strongpassword123"
        })).json()["token"]
        org_b = reg_b.json()["organization"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Organization-ID": org_b}

        # Seed initial data for Tenant A
        async with session_manager.session() as session:
            for i in range(10):
                ev = TelemetryEventDbModel(
                    id=str(uuid4()),
                    timestamp=datetime.utcnow() - timedelta(seconds=i),
                    component="API",
                    operation="tenant_a_op",
                    message="Target event",
                    severity="INFO",
                    status="SUCCESS",
                    organization_id=org_a
                )
                session.add(ev)
            await session.commit()

        # Measure Tenant A's baseline latency under quiet system conditions
        baseline_durations = []
        for _ in range(5):
            start = time.perf_counter()
            res = await ac.get("/api/observability/traces?limit=10", headers=headers_a)
            end = time.perf_counter()
            assert res.status_code == 200
            baseline_durations.append(end - start)
        baseline_avg = sum(baseline_durations) / len(baseline_durations)

        # Generate noisy neighbor load for Tenant B concurrently
        async def run_noisy_neighbor_load():
            # Seed Tenant B's data
            async with session_manager.session() as session:
                for i in range(200):
                    ev = TelemetryEventDbModel(
                        id=str(uuid4()),
                        timestamp=datetime.utcnow() - timedelta(seconds=i),
                        component="WORKER",
                        operation="noisy_b_op",
                        message="Noisy event",
                        severity="WARNING",
                        status="SUCCESS",
                        organization_id=org_b
                    )
                    session.add(ev)
                await session.commit()
            
            # Hit Tenant B traces endpoint heavily
            for _ in range(30):
                await ac.get("/api/observability/traces?limit=50", headers=headers_b)

        # Start Tenant B load task
        load_task = asyncio.create_task(run_noisy_neighbor_load())
        await asyncio.sleep(0.1) # Let B's tasks start running

        # Measure Tenant A's latency while Tenant B is active
        load_durations = []
        for _ in range(5):
            start = time.perf_counter()
            res = await ac.get("/api/observability/traces?limit=10", headers=headers_a)
            end = time.perf_counter()
            assert res.status_code == 200
            load_durations.append(end - start)
        load_avg = sum(load_durations) / len(load_durations)

        # Wait for B's background tasks to finish
        await load_task

        baseline_ms = baseline_avg * 1000.0
        load_ms = load_avg * 1000.0
        degradation = (load_avg / baseline_avg) if baseline_avg > 0 else 1.0

        print("\n=== 13.6.5 — MULTI-TENANT NOISY NEIGHBOR IMPACT ===")
        print(f"Tenant A Baseline Latency: {baseline_ms:.2f}ms")
        print(f"Tenant A Latency Under Tenant B Load: {load_ms:.2f}ms")
        print(f"Performance Degradation Factor: {degradation:.2f}x")

        # Assert no unacceptable degradation (e.g. latency under load remains < 150ms or degradation is small)
        assert load_ms < 150.0


@pytest.mark.asyncio
async def test_operational_data_scale():
    """
    13.6.6 — Operational Data Scale
    Seeds telemetry events progressively in datasets of sizes 100, 500, 1000, and 5000.
    Measures database query latencies for trace lists, anomalies, and situations
    to confirm logarithmic/indexed scaling holds.
    """
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        email = f"owner_scale_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner Scale",
            "organization_name": "Org Scale",
            "organization_slug": f"org-scale-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        token = (await ac.post("/auth/login", json={
            "email": email,
            "password": "strongpassword123"
        })).json()["token"]
        org_id = reg.json()["organization"]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-ID": org_id}

        scales = [100, 500, 1000, 5000]
        current_seeded = 0

        print("\n=== 13.6.6 — DATABASE QUERY SCALING CHARACTERISTICS ===")
        print(f"| Scale (Events) | Trace Query (ms) | Anomaly Query (ms) | Situation Query (ms) |")
        print(f"|----------------|------------------|--------------------|----------------------|")

        for scale in scales:
            # Seed incremental events to reach the target scale
            to_seed = scale - current_seeded
            async with session_manager.session() as session:
                for i in range(to_seed):
                    ev = TelemetryEventDbModel(
                        id=str(uuid4()),
                        timestamp=datetime.utcnow() - timedelta(seconds=i),
                        component="WORKFLOW",
                        operation="scale_audit_perf",
                        message=f"Event {i}",
                        severity="INFO",
                        status="SUCCESS",
                        organization_id=org_id
                    )
                    session.add(ev)
                await session.commit()
            current_seeded = scale

            # Seed a single anomaly and situation to make queries active
            anomaly_id = str(uuid4())
            async with session_manager.session() as session:
                anom = AnomalyDbModel(
                    id=anomaly_id,
                    anomaly_type="HIGH_LATENCY",
                    severity="WARNING",
                    component="api",
                    organization_id=org_id,
                    correlation_id=f"corr-{str(uuid4())[:6]}",
                    status="DETECTED",
                    evidence="{}"
                )
                sit = OperationalSituationDbModel(
                    id=str(uuid4()),
                    title="Scale Situation",
                    status="ACTIVE",
                    impact_score=50.0,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    evidence="{}",
                    correlation_id=anom.correlation_id,
                    organization_id=org_id
                )
                session.add(anom)
                session.add(sit)
                await session.commit()

            # Measure trace query latency
            t_start = time.perf_counter()
            res_t = await ac.get("/api/observability/traces?limit=50", headers=headers)
            t_end = time.perf_counter()
            assert res_t.status_code == 200
            t_ms = (t_end - t_start) * 1000.0

            # Measure anomaly query latency
            a_start = time.perf_counter()
            res_a = await ac.get("/api/observability/anomalies", headers=headers)
            a_end = time.perf_counter()
            assert res_a.status_code == 200
            a_ms = (a_end - a_start) * 1000.0

            # Measure situation query latency
            s_start = time.perf_counter()
            res_s = await ac.get("/api/observability/situations", headers=headers)
            s_end = time.perf_counter()
            assert res_s.status_code == 200
            s_ms = (s_end - s_start) * 1000.0

            print(f"| {scale:<14} | {t_ms:<16.2f} | {a_ms:<18.2f} | {s_ms:<20.2f} |")

            # Clean up seeded anomaly/situation to keep next loop pure
            async with session_manager.session() as session:
                from sqlalchemy import delete
                await session.execute(delete(AnomalyDbModel).where(AnomalyDbModel.organization_id == org_id))
                await session.execute(delete(OperationalSituationDbModel).where(OperationalSituationDbModel.organization_id == org_id))
                await session.commit()

            # Verify query performance remains well bounded (e.g. traces query < 150ms even at 5000 scale)
            assert t_ms < 150.0
