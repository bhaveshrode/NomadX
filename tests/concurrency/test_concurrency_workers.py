import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy import select
from nomadx.apps.api.main import app
from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OutboxEventDbModel
from nomadx.src.infrastructure.workers.ar_event_processor import AREventProcessor

@pytest.mark.asyncio
async def test_concurrent_worker_claiming():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register and login
        email = f"owner_wconc_{uuid4().hex[:6]}@example.com"
        reg = await ac.post("/auth/register", json={
            "email": email,
            "password": "strongpassword123",
            "name": "Owner WConc",
            "organization_name": "Org WConc Test",
            "organization_slug": f"org-wconc-{uuid4().hex[:6]}",
            "default_currency": "USDC"
        })
        org_id = reg.json()["organization"]["id"]

        # Seed 5 Outbox events of type "SettlementCompleted"
        async with session_manager.session() as session:
            for _ in range(5):
                ev = OutboxEventDbModel(
                    id=str(uuid4()),
                    event_type="SettlementCompleted",
                    payload="{}",
                    processed=False,
                    status="PENDING",
                    created_at=datetime.utcnow()
                )
                session.add(ev)
            await session.commit()

        ar_processor = AREventProcessor()

        # Fire 5 concurrent workers attempting to claim the events
        results = await asyncio.gather(
            ar_processor.process_payment_events(),
            ar_processor.process_payment_events(),
            ar_processor.process_payment_events(),
            ar_processor.process_payment_events(),
            ar_processor.process_payment_events()
        )

        # Verify that all 5 events were successfully processed and updated in the database
        async with session_manager.session() as session:
            res_events = await session.execute(
                select(OutboxEventDbModel).where(OutboxEventDbModel.status == "COMPLETED")
            )
            db_events = res_events.scalars().all()
            assert len(db_events) == 5
            for ev in db_events:
                assert ev.processed is True
