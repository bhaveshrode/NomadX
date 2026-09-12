import asyncio
import json
import logging
import signal
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager
from nomadx.src.infrastructure.database.models import OutboxEventDbModel
from nomadx.src.infrastructure.blockchain.reconciliation_worker import ReconciliationWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NomadXWorker")


async def process_outbox_events(session: AsyncSession) -> int:
    """
    Polls the database for unprocessed outbox events, simulates publishing them,
    and marks them as processed in a single transaction.
    """
    result = await session.execute(
        select(OutboxEventDbModel)
        .where(OutboxEventDbModel.processed == False)  # noqa: E712
        .order_by(OutboxEventDbModel.created_at.asc())
    )
    events = result.scalars().all()
    
    if not events:
        return 0

    logger.info(f"Outbox publisher found {len(events)} unprocessed outbox events.")

    for event in events:
        try:
            payload = json.loads(event.payload)
            logger.info(
                f"Outbox publisher publishing event '{event.event_type}' (ID: {event.id}) "
                f"occurred at {event.created_at} for aggregate ID {payload.get('organization_id')}"
            )
            event.processed = True
            event.processed_at = datetime.utcnow()
        except Exception as ex:
            logger.error(f"Failed to process outbox event {event.id}: {ex}")

    await session.flush()
    return len(events)


async def outbox_tick() -> int:
    """Outbox publisher helper execution tick."""
    async with session_manager.session() as session:
        count = await process_outbox_events(session)
        if count > 0:
            await session.commit()
        return count


async def run_intelligence_sweeps() -> int:
    """Continuous closed-loop operational intelligence sweep loop helper."""
    try:
        async with session_manager.session() as session:
            from nomadx.src.infrastructure.database.repositories import PostgresOrganizationRepository
            org_repo = PostgresOrganizationRepository(session)
            orgs = await org_repo.find_all()
    except Exception as e:
        logger.error(f"Failed to fetch active organizations for intelligence sweeps: {e}")
        return 0

    success_count = 0
    for org in orgs:
        try:
            async with container.observability_query_service() as obs_svc:
                res = await obs_svc.trigger_intelligence_tick(org.id, actor_id="system_worker")
                logger.info(f"Intelligence tick executed for org {org.id.value}: {res}")
                success_count += 1
        except Exception as e:
            logger.error(f"Error running intelligence tick for org {org.id.value}: {e}")
    return success_count


async def run_worker_loop_item(name: str, interval: float, func, stop_event: asyncio.Event):
    """Executes a single periodic worker loop with isolated error handling and shutdown event checks."""
    logger.info(f"Loop '{name}' started (polling every {interval}s).")
    while not stop_event.is_set():
        try:
            print(f"--- LOOP TICK START: '{name}'")
            processed = await func()
            print(f"--- LOOP TICK END: '{name}' (processed={processed})")
            if processed and isinstance(processed, int) and processed > 0:
                logger.info(f"Loop '{name}' processed {processed} item(s).")
        except Exception as e:
            print(f"--- LOOP TICK EXCEPTION: '{name}': {e}")
            logger.error(f"Unhandled exception in background loop '{name}': {e}", exc_info=True)
        
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info(f"Loop '{name}' cleanly stopped.")


async def run_worker_loop():
    """Main worker startup bootstrap coordinating all parallel asyncio task loops."""
    logger.info("Initializing NomadX Worker resources...")
    container.init_resources()
    
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    
    def shutdown_handler(sig):
        logger.info(f"Received signal {sig.name if hasattr(sig, 'name') else sig}, initiating graceful shutdown...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: shutdown_handler(s))
        except NotImplementedError:
            # Fallback for Windows where SIGTERM/SIGINT loop signal handlers are not supported
            pass

    # Open contexts for dependent workers cleanly
    async with container.ar_event_processor() as ar_proc, \
               container.settlement_event_processor() as set_evt_proc, \
               container.workflow_worker() as wf_worker, \
               container.dunning_worker() as dun_worker, \
               container.collection_action_worker() as col_act_worker, \
               container.notification_worker() as notif_worker, \
               container.notification_event_worker() as notif_evt_worker, \
               container.settlement_worker() as set_worker, \
               container.settlement_verification_worker() as set_ver_worker, \
               container.settlement_automation_worker() as set_auto_worker, \
               container.financial_consistency_automation_worker() as fin_const_worker:

        recon_worker = ReconciliationWorker()

        # Define loop items
        tasks = [
            # asyncio.create_task(run_worker_loop_item("outbox_publisher", 10.0, outbox_tick, stop_event)),
            asyncio.create_task(run_worker_loop_item("reconciliation_worker", 2.0, recon_worker.process_pending_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("ar_event_processor", 2.0, ar_proc.process_payment_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("ar_event_recovery", 60.0, ar_proc.recover_stuck_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("settlement_event_processor", 2.0, set_evt_proc.process_financial_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("settlement_event_recovery", 60.0, set_evt_proc.recover_stuck_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("workflow_worker", 2.0, wf_worker.process_pending_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("dunning_worker", 20.0, dun_worker.process_dunning, stop_event)),
            asyncio.create_task(run_worker_loop_item("collection_action_worker", 20.0, col_act_worker.process_actions, stop_event)),
            asyncio.create_task(run_worker_loop_item("collection_action_recovery", 60.0, col_act_worker.recover_stuck_actions, stop_event)),
            asyncio.create_task(run_worker_loop_item("notification_worker", 10.0, notif_worker.process_pending_notifications, stop_event)),
            asyncio.create_task(run_worker_loop_item("notification_event_worker", 2.0, notif_evt_worker.process_pending_events, stop_event)),
            asyncio.create_task(run_worker_loop_item("settlement_worker", 20.0, set_worker.process_pending_settlements, stop_event)),
            asyncio.create_task(run_worker_loop_item("settlement_verification_worker", 20.0, set_ver_worker.process_submitted_settlements, stop_event)),
            asyncio.create_task(run_worker_loop_item("settlement_automation_worker", 2.0, set_auto_worker.process_eligible_settlements, stop_event)),
            asyncio.create_task(run_worker_loop_item("financial_consistency_worker", 60.0, fin_const_worker.run_integrity_sweep, stop_event)),
            asyncio.create_task(run_worker_loop_item("observability_intelligence_loop", 60.0, run_intelligence_sweeps, stop_event))
        ]

        logger.info(f"NomadX Background Scheduler running. Coordinating {len(tasks)} background loops.")
        
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("Main background worker scheduler was cancelled.")
        finally:
            # Signal stop to all workers
            stop_event.set()
            # Await any remaining task cleanup
            await asyncio.gather(*tasks, return_exceptions=True)
            await container.close_resources()
            logger.info("Worker resources cleanly disposed. Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user (KeyboardInterrupt).")
