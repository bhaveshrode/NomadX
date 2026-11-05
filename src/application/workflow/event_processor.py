from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from uuid import uuid4
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.shared.clock.clock import Clock
from ...domain.organization.value_objects import OrganizationId
from ...domain.workflow.value_objects import WorkflowId, WorkflowVersion, WorkflowStatus
from ...domain.workflow.execution import WorkflowExecution, WorkflowExecutionId
from ...domain.workflow.actions import WorkflowAction
from ...infrastructure.database.session import session_manager
from ...infrastructure.database.models import (
    OutboxEventDbModel,
    WorkflowDbModel,
    WorkflowVersionDbModel,
    WorkflowExecutionDbModel,
    WorkflowCursorDbModel,
    WorkflowScheduledActionDbModel
)
from ...infrastructure.database.repositories import PostgresWorkflowRepository, PostgresWorkflowExecutionRepository
from ...presentation.api.dependencies.context import ExecutionContext
from .condition_evaluator import ConditionEvaluator
from .action_handlers import WorkflowActionRegistry
from .variable_resolver import ParameterTemplater

logger = logging.getLogger(__name__)


class WorkflowEventProcessor:
    """
    Core engine orchestrating event intake, logic condition evaluation, crash recovery,
    action scheduling, and automatic retry backoffs with dead-lettering.
    """
    def __init__(self, consumer_name: str = "workflow_engine"):
        self._consumer_name = consumer_name
        self._condition_evaluator = ConditionEvaluator()
        self._action_registry = WorkflowActionRegistry()

    async def process_events(self) -> int:
        """Runs the complete event loop processing new outbox events and retry/schedule cycles."""
        processed_count = 0
        
        # 1. Recover stuck executions (Crash Recovery)
        await self._recover_stuck_executions()

        # 2. Process new events from transactional outbox
        processed_count += await self._process_new_outbox_events()

        # 3. Process due retries
        processed_count += await self._process_due_retries()

        # 4. Process due scheduled actions
        processed_count += await self._process_due_scheduled_actions()

        return processed_count

    async def _process_new_outbox_events(self) -> int:
        processed_count = 0
        async with session_manager.session() as session:
            execution_repo = PostgresWorkflowExecutionRepository(session)
            workflow_repo = PostgresWorkflowRepository(session)

            # Fetch cursor checkpoint
            last_timestamp = await execution_repo.get_cursor(self._consumer_name)
            if not last_timestamp:
                last_timestamp = datetime.utcnow() - timedelta(hours=24)

            # Query new outbox events
            stmt_events = (
                select(OutboxEventDbModel)
                .where(OutboxEventDbModel.created_at > last_timestamp)
                .order_by(OutboxEventDbModel.created_at.asc())
            )
            res_events = await session.execute(stmt_events)
            outbox_events = res_events.scalars().all()
            if not outbox_events:
                return 0

            # Load active workflows
            stmt_wfs = select(WorkflowDbModel).where(WorkflowDbModel.status == "ACTIVE")
            res_wfs = await session.execute(stmt_wfs)
            active_wf_models = res_wfs.scalars().all()
            if not active_wf_models:
                # No active workflows. Advance cursor and finish
                await execution_repo.save_cursor(self._consumer_name, outbox_events[-1].created_at)
                await session.commit()
                return 0

            for event in outbox_events:
                payload = json.loads(event.payload)
                org_id = payload.get("organization_id")
                if not org_id:
                    continue

                # Parse event loop ancestry recursion protections
                metadata = payload.get("_metadata", {})
                correlation_id = metadata.get("correlation_id") or str(uuid4())
                ancestry = list(metadata.get("ancestry", []))

                # Identify matching workflows
                for wf_model in active_wf_models:
                    if wf_model.organization_id != org_id:
                        continue

                    # Fetch active version details
                    active_ver_num = wf_model.active_version_number
                    stmt_ver = select(WorkflowVersionDbModel).where(
                        WorkflowVersionDbModel.workflow_id == wf_model.id,
                        WorkflowVersionDbModel.version == active_ver_num
                    )
                    res_ver = await session.execute(stmt_ver)
                    ver_model = res_ver.scalar_one_or_none()
                    if not ver_model:
                        continue

                    # Match trigger
                    trigger_data = json.loads(ver_model.trigger)
                    if trigger_data.get("event_type") != event.event_type:
                        continue

                    # Match conditions
                    wf = await workflow_repo.get_by_id(WorkflowId(wf_model.id))
                    if not wf:
                        continue
                    definition = wf.definitions.get(active_ver_num)
                    if not definition:
                        continue

                    if definition.conditions:
                        matched = self._condition_evaluator.evaluate_group(definition.conditions, payload)
                        if not matched:
                            continue

                    # Idempotency check: bypass duplicate event deliveries
                    existing_exec = await execution_repo.find_execution(
                        WorkflowId(wf_model.id),
                        WorkflowVersion(active_ver_num),
                        event.id
                    )
                    if existing_exec:
                        continue

                    # 1. Recursion Loop Protection Safeguard check
                    if len(ancestry) >= 5:
                        logger.error(
                            f"Workflow recursion loop detected: Event {event.id} already has an ancestry chain "
                            f"of length {len(ancestry)}: {ancestry}. Aborting workflow execution."
                        )
                        # Create a DEAD_LETTERED execution straight away
                        exec_id = WorkflowExecutionId(str(uuid4()))
                        exec_res = WorkflowExecution.start(
                            id=exec_id,
                            workflow_id=WorkflowId(wf_model.id),
                            version=WorkflowVersion(active_ver_num),
                            source_event_id=event.id,
                            source_event_type=event.event_type
                        )
                        execution = exec_res.value
                        execution.dead_letter("Workflow recursion loop detected.")
                        await execution_repo.save(execution)
                        processed_count += 1
                        continue

                    # Construct execution context
                    ctx = ExecutionContext(
                        user_id="workflow_engine",
                        organization_id=org_id,
                        role="SYSTEM"
                    )

                    # Start new execution aggregate
                    exec_id = WorkflowExecutionId(str(uuid4()))
                    exec_res = WorkflowExecution.start(
                        id=exec_id,
                        workflow_id=WorkflowId(wf_model.id),
                        version=WorkflowVersion(active_ver_num),
                        source_event_id=event.id,
                        source_event_type=event.event_type
                    )
                    execution = exec_res.value

                    # Execute actions loop
                    await self._execute_workflow_pipeline(
                        execution=execution,
                        definition=definition,
                        session=session,
                        execution_repo=execution_repo,
                        payload=payload,
                        ctx=ctx,
                        org_id=org_id,
                        event=event,
                        correlation_id=correlation_id,
                        ancestry=ancestry + [exec_id.value]
                    )
                    processed_count += 1

            # Advance cursor checkpoint timestamp
            await execution_repo.save_cursor(self._consumer_name, outbox_events[-1].created_at)
            await session.commit()

        return processed_count

    async def _execute_workflow_pipeline(
        self,
        execution: WorkflowExecution,
        definition: Any,
        session: AsyncSession,
        execution_repo: PostgresWorkflowExecutionRepository,
        payload: Dict[str, Any],
        ctx: ExecutionContext,
        org_id: str,
        event: Any,
        correlation_id: str,
        ancestry: List[str]
    ) -> None:
        """Runs or schedules actions in the pipeline using transaction savepoints."""
        has_scheduled = False
        action_failed = False
        retry_requested = False
        failure_msg = ""

        # Loop through actions to check scheduling or run immediately
        for idx, action in enumerate(definition.actions):
            # Check if this action is scheduled
            delay_seconds = action.parameters.get("delay_seconds")
            if delay_seconds is not None:
                # Schedule action
                scheduled_id = str(uuid4())
                scheduled_db = WorkflowScheduledActionDbModel(
                    id=scheduled_id,
                    execution_id=execution.id.value,
                    organization_id=org_id,
                    action_index=idx,
                    action_data=json.dumps(dict(action.parameters)),
                    scheduled_at=Clock.now() + timedelta(seconds=int(delay_seconds)),
                    status="PENDING"
                )
                session.add(scheduled_db)
                has_scheduled = True
                execution.record_action_result(action.action_type.name, "SCHEDULED", f"Scheduled for index {idx}", duration=0.0)
                continue

            # Run immediate action with nested savepoint transaction
            start_time = Clock.now()
            try:
                handler = self._action_registry.get(action.action_type.name)
                # Resolve variables/templates dynamically
                resolved_params = ParameterTemplater.resolve_template(dict(action.parameters), payload)
                
                # Dynamic action context
                action_context = {
                    "organization_id": org_id,
                    "workflow_id": execution.workflow_id.value,
                    "workflow_version": execution.version.value,
                    "execution_id": execution.id.value,
                    "source_event_id": event.id,
                    "source_event_type": event.event_type,
                    "action_id": f"{execution.id.value}-action-{idx}",
                    "action_index": idx,
                    "correlation_id": correlation_id,
                    "ancestry": ancestry
                }

                async with session.begin_nested():
                    res = await handler.execute(
                        parameters=resolved_params,
                        session=session,
                        execution_context=action_context,
                        payload=payload,
                        ctx=ctx
                    )
                duration = (Clock.now() - start_time).total_seconds()
                execution.record_action_result(action.action_type.name, "SUCCESS", json.dumps(res), duration=duration)
            except Exception as err:
                duration = (Clock.now() - start_time).total_seconds()
                failure_msg = str(err)
                execution.record_action_result(action.action_type.name, "FAILED", failure_msg, duration=duration)
                
                # Check configurable failure policy
                failure_policy = action.parameters.get("failure_policy", "FAIL_WORKFLOW")
                if failure_policy == "CONTINUE_WORKFLOW":
                    # Ignore action failure and proceed
                    continue
                elif failure_policy == "RETRY_ACTION":
                    action_failed = True
                    retry_requested = True
                    break
                else:  # FAIL_WORKFLOW
                    action_failed = True
                    retry_requested = True
                    break

        if action_failed:
            if retry_requested:
                execution.fail(failure_msg, is_retryable=True)
            else:
                execution.fail(failure_msg, is_retryable=False)
        elif not has_scheduled:
            # Completed immediately
            execution.complete()

        await execution_repo.save(execution)

    async def _process_due_retries(self) -> int:
        processed_count = 0
        async with session_manager.session() as session:
            # Query for executions in RETRYING status where next_retry_at is due
            # Apply pessimistic locking (skip locked workers)
            stmt = (
                select(WorkflowExecutionDbModel)
                .where(WorkflowExecutionDbModel.status == "RETRYING")
                .where(
                    or_(
                        WorkflowExecutionDbModel.next_retry_at.is_(None),
                        WorkflowExecutionDbModel.next_retry_at <= Clock.now()
                    )
                )
                .with_for_update(skip_locked=True)
            )
            res = await session.execute(stmt)
            execution_models = res.scalars().all()
            if not execution_models:
                return 0

            execution_repo = PostgresWorkflowExecutionRepository(session)
            workflow_repo = PostgresWorkflowRepository(session)

            for m in execution_models:
                # Load corresponding workflow version
                stmt_ver = select(WorkflowVersionDbModel).where(
                    WorkflowVersionDbModel.workflow_id == m.workflow_id,
                    WorkflowVersionDbModel.version == m.version
                )
                res_ver = await session.execute(stmt_ver)
                ver_model = res_ver.scalar_one_or_none()
                if not ver_model:
                    continue

                # Load original outbox event payload
                stmt_event = select(OutboxEventDbModel).where(OutboxEventDbModel.id == m.source_event_id)
                res_event = await session.execute(stmt_event)
                event = res_event.scalar_one_or_none()
                if not event:
                    continue

                payload = json.loads(event.payload)

                wf = await workflow_repo.get_by_id(WorkflowId(m.workflow_id))
                if not wf:
                    continue

                definition = wf.definitions.get(m.version)
                if not definition:
                    continue

                # Construct execution context
                ctx = ExecutionContext(
                    user_id="workflow_engine",
                    organization_id=wf.organization_id.value,
                    role="SYSTEM"
                )

                # Reconstruct domain model to perform retry
                execution = await execution_repo.get_by_id(WorkflowExecutionId(m.id))
                if not execution:
                    continue

                # Event loop ancestry propagation
                metadata = payload.get("_metadata", {})
                correlation_id = metadata.get("correlation_id") or str(uuid4())
                ancestry = list(metadata.get("ancestry", []))

                # Determine where to resume (action-level idempotency)
                completed_indices = {
                    res["action_type"] for res in execution.action_results if res.get("status") == "SUCCESS"
                }

                action_failed = False
                retry_requested = False
                failure_msg = ""
                for idx, action in enumerate(definition.actions):
                    if action.action_type.name in completed_indices:
                        continue

                    # If it's a scheduled action, reset scheduled action status to PENDING
                    delay_seconds = action.parameters.get("delay_seconds")
                    if delay_seconds is not None:
                        stmt_sa = select(WorkflowScheduledActionDbModel).where(
                            WorkflowScheduledActionDbModel.execution_id == execution.id.value,
                            WorkflowScheduledActionDbModel.action_index == idx
                        )
                        sa_res = await session.execute(stmt_sa)
                        sa_model = sa_res.scalar_one_or_none()
                        if sa_model:
                            sa_model.status = "PENDING"
                            sa_model.scheduled_at = Clock.now() + timedelta(seconds=int(delay_seconds))
                        continue

                    # Run immediate action
                    start_time = Clock.now()
                    try:
                        handler = self._action_registry.get(action.action_type.name)
                        resolved_params = ParameterTemplater.resolve_template(dict(action.parameters), payload)
                        
                        action_context = {
                            "organization_id": wf.organization_id.value,
                            "workflow_id": execution.workflow_id.value,
                            "workflow_version": execution.version.value,
                            "execution_id": execution.id.value,
                            "source_event_id": event.id,
                            "source_event_type": event.event_type,
                            "action_id": f"{execution.id.value}-action-{idx}",
                            "action_index": idx,
                            "correlation_id": correlation_id,
                            "ancestry": ancestry
                        }

                        async with session.begin_nested():
                            res = await handler.execute(
                                parameters=resolved_params,
                                session=session,
                                execution_context=action_context,
                                payload=payload,
                                ctx=ctx
                            )
                        duration = (Clock.now() - start_time).total_seconds()
                        execution.record_action_result(action.action_type.name, "SUCCESS", json.dumps(res), duration=duration)
                    except Exception as err:
                        duration = (Clock.now() - start_time).total_seconds()
                        failure_msg = str(err)
                        execution.record_action_result(action.action_type.name, "FAILED", failure_msg, duration=duration)
                        
                        # Check configurable failure policy
                        failure_policy = action.parameters.get("failure_policy", "FAIL_WORKFLOW")
                        if failure_policy == "CONTINUE_WORKFLOW":
                            continue
                        elif failure_policy == "RETRY_ACTION":
                            action_failed = True
                            retry_requested = True
                            break
                        else:  # FAIL_WORKFLOW
                            action_failed = True
                            retry_requested = True
                            break

                if action_failed:
                    if retry_requested:
                        execution.fail(failure_msg, is_retryable=True)
                    else:
                        execution.fail(failure_msg, is_retryable=False)
                else:
                    # Check if all completed (none pending scheduled)
                    stmt_pending = select(WorkflowScheduledActionDbModel).where(
                        WorkflowScheduledActionDbModel.execution_id == execution.id.value,
                        WorkflowScheduledActionDbModel.status == "PENDING"
                    )
                    pending_sa = (await session.execute(stmt_pending)).scalars().all()
                    if not pending_sa:
                        execution.complete()
                    else:
                        # Transition status back to STARTED while awaiting scheduled actions
                        execution._props["status"] = "STARTED"
                        execution._props["next_retry_at"] = None

                await execution_repo.save(execution)
                processed_count += 1

            await session.commit()
            return processed_count

    async def _process_due_scheduled_actions(self) -> int:
        processed_count = 0
        async with session_manager.session() as session:
            # Query due scheduled actions
            stmt = (
                select(WorkflowScheduledActionDbModel)
                .where(WorkflowScheduledActionDbModel.status == "PENDING")
                .where(WorkflowScheduledActionDbModel.scheduled_at <= Clock.now())
                .with_for_update(skip_locked=True)
            )
            res = await session.execute(stmt)
            scheduled_actions = res.scalars().all()
            if not scheduled_actions:
                return 0

            execution_repo = PostgresWorkflowExecutionRepository(session)
            workflow_repo = PostgresWorkflowRepository(session)

            for sa in scheduled_actions:
                # Load execution and workflow version
                stmt_exec = select(WorkflowExecutionDbModel).where(WorkflowExecutionDbModel.id == sa.execution_id)
                res_exec = await session.execute(stmt_exec)
                exec_model = res_exec.scalar_one_or_none()
                if not exec_model:
                    sa.status = "FAILED"
                    continue

                stmt_ver = select(WorkflowVersionDbModel).where(
                    WorkflowVersionDbModel.workflow_id == exec_model.workflow_id,
                    WorkflowVersionDbModel.version == exec_model.version
                )
                res_ver = await session.execute(stmt_ver)
                ver_model = res_ver.scalar_one_or_none()
                if not ver_model:
                    sa.status = "FAILED"
                    continue

                # Load original outbox event payload
                stmt_event = select(OutboxEventDbModel).where(OutboxEventDbModel.id == exec_model.source_event_id)
                res_event = await session.execute(stmt_event)
                event = res_event.scalar_one_or_none()
                if not event:
                    sa.status = "FAILED"
                    continue

                payload = json.loads(event.payload)

                wf = await workflow_repo.get_by_id(WorkflowId(exec_model.workflow_id))
                if not wf:
                    sa.status = "FAILED"
                    continue

                definition = wf.definitions.get(exec_model.version)
                if not definition or sa.action_index >= len(definition.actions):
                    sa.status = "FAILED"
                    continue

                action = definition.actions[sa.action_index]
                execution = await execution_repo.get_by_id(WorkflowExecutionId(sa.execution_id))
                if not execution:
                    sa.status = "FAILED"
                    continue

                # Context propagation
                metadata = payload.get("_metadata", {})
                correlation_id = metadata.get("correlation_id") or str(uuid4())
                ancestry = list(metadata.get("ancestry", []))

                # Construct execution context
                ctx = ExecutionContext(
                    user_id="workflow_engine",
                    organization_id=wf.organization_id.value,
                    role="SYSTEM"
                )

                # Execute action
                start_time = Clock.now()
                try:
                    handler = self._action_registry.get(action.action_type.name)
                    resolved_params = ParameterTemplater.resolve_template(dict(action.parameters), payload)
                    
                    action_context = {
                        "organization_id": wf.organization_id.value,
                        "workflow_id": execution.workflow_id.value,
                        "workflow_version": execution.version.value,
                        "execution_id": execution.id.value,
                        "source_event_id": event.id,
                        "source_event_type": event.event_type,
                        "action_id": f"{execution.id.value}-action-{sa.action_index}",
                        "action_index": sa.action_index,
                        "correlation_id": correlation_id,
                        "ancestry": ancestry
                    }

                    async with session.begin_nested():
                        res = await handler.execute(
                            parameters=resolved_params,
                            session=session,
                            execution_context=action_context,
                            payload=payload,
                            ctx=ctx
                        )
                    sa.status = "COMPLETED"
                    sa.executed_at = Clock.now()
                    duration = (Clock.now() - start_time).total_seconds()
                    execution.record_action_result(action.action_type.name, "SUCCESS", json.dumps(res), duration=duration)
                except Exception as err:
                    sa.status = "FAILED"
                    sa.executed_at = Clock.now()
                    duration = (Clock.now() - start_time).total_seconds()
                    execution.record_action_result(action.action_type.name, "FAILED", str(err), duration=duration)
                    
                    # Apply failure policy checks for delayed actions as well
                    failure_policy = action.parameters.get("failure_policy", "FAIL_WORKFLOW")
                    if failure_policy == "CONTINUE_WORKFLOW":
                        sa.status = "COMPLETED" # continue execution
                    elif failure_policy == "RETRY_ACTION":
                        execution.fail(str(err), is_retryable=True)
                    else: # FAIL_WORKFLOW
                        execution.fail(str(err), is_retryable=False)

                # Check if all other actions for this execution are complete/failed
                if sa.status == "COMPLETED":
                    stmt_pending = select(WorkflowScheduledActionDbModel).where(
                        WorkflowScheduledActionDbModel.execution_id == execution.id.value,
                        WorkflowScheduledActionDbModel.status == "PENDING",
                        WorkflowScheduledActionDbModel.id != sa.id
                    )
                    pending_sa = (await session.execute(stmt_pending)).scalars().all()
                    if not pending_sa:
                        execution.complete()

                await execution_repo.save(execution)
                processed_count += 1

            await session.commit()
            return processed_count

    async def _recover_stuck_executions(self) -> None:
        """Finds and claims executions stuck in STARTED state for more than 10 minutes."""
        async with session_manager.session() as session:
            cutoff = Clock.now() - timedelta(minutes=10)
            stmt = (
                select(WorkflowExecutionDbModel)
                .where(WorkflowExecutionDbModel.status == "STARTED")
                .where(WorkflowExecutionDbModel.started_at <= cutoff)
                .with_for_update(skip_locked=True)
            )
            res = await session.execute(stmt)
            stuck_models = res.scalars().all()
            if not stuck_models:
                return

            execution_repo = PostgresWorkflowExecutionRepository(session)
            for m in stuck_models:
                execution = await execution_repo.get_by_id(WorkflowExecutionId(m.id))
                if execution:
                    # Transition to failed/retryable to trigger automatic worker retry
                    execution.fail("Recovered from stuck execution (Worker crash recovery check).", is_retryable=True)
                    await execution_repo.save(execution)

            await session.commit()
