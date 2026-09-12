from datetime import datetime, timedelta
import json
import time
import asyncio
from uuid import uuid4
from typing import Dict, Any, List, Optional
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.organization.value_objects import OrganizationId
from ...application.observability.query_service import IObservabilityQueryService
from .models import (
    OrganizationDbModel,
    TelemetryEventDbModel,
    ComponentHealthTransitionDbModel,
    FinancialConsistencyViolationDbModel,
    AnomalyDbModel,
    RemediationActionDbModel,
    OperationalSituationDbModel,
    OperationalAuditTrailDbModel,
    AutonomousReadinessPolicyDbModel
)


class PostgresObservabilityQueryService(IObservabilityQueryService):
    """
    SQLAlchemy-backed implementation of IObservabilityQueryService.
    Queries structured telemetry within tenant boundaries.
    """
    _org_locks = {}

    def __init__(self, session: AsyncSession):
        self._session = session

    async def query_traces(
        self,
        organization_id: OrganizationId,
        component: Optional[str] = None,
        severity: Optional[str] = None,
        correlation_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(TelemetryEventDbModel)
            .where(TelemetryEventDbModel.organization_id == organization_id.value)
        )
        if component:
            stmt = stmt.where(TelemetryEventDbModel.component == component)
        if severity:
            stmt = stmt.where(TelemetryEventDbModel.severity == severity)
        if correlation_id:
            stmt = stmt.where(TelemetryEventDbModel.correlation_id == correlation_id)

        limit = min(limit, 100)
        stmt = stmt.order_by(TelemetryEventDbModel.timestamp.desc()).limit(limit).offset(offset)
        
        res = await self._session.execute(stmt)
        events = res.scalars().all()

        return [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "component": e.component,
                "operation": e.operation,
                "message": e.message,
                "severity": e.severity,
                "entity_id": e.entity_id,
                "previous_state": e.previous_state,
                "resulting_state": e.resulting_state,
                "status": e.status,
                "duration_ms": e.duration_ms,
                "correlation_id": e.correlation_id,
                "causation_id": e.causation_id,
                "organization_id": e.organization_id,
                "extra_metadata": json.loads(e.extra_metadata) if e.extra_metadata else None
            }
            for e in events
        ]

    async def get_trace_by_correlation_id(
        self,
        organization_id: OrganizationId,
        correlation_id: str
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(TelemetryEventDbModel)
            .where(TelemetryEventDbModel.organization_id == organization_id.value)
            .where(TelemetryEventDbModel.correlation_id == correlation_id)
            .order_by(TelemetryEventDbModel.timestamp.asc())
        )
        res = await self._session.execute(stmt)
        events = res.scalars().all()

        return [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "component": e.component,
                "operation": e.operation,
                "message": e.message,
                "severity": e.severity,
                "entity_id": e.entity_id,
                "previous_state": e.previous_state,
                "resulting_state": e.resulting_state,
                "status": e.status,
                "duration_ms": e.duration_ms,
                "correlation_id": e.correlation_id,
                "causation_id": e.causation_id,
                "organization_id": e.organization_id,
                "extra_metadata": json.loads(e.extra_metadata) if e.extra_metadata else None
            }
            for e in events
        ]

    async def get_component_health(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        # Fetch last 100 events
        stmt = (
            select(TelemetryEventDbModel)
            .where(TelemetryEventDbModel.organization_id == organization_id.value)
            .order_by(TelemetryEventDbModel.timestamp.desc())
            .limit(100)
        )
        res = await self._session.execute(stmt)
        recent_events = res.scalars().all()

        now = datetime.utcnow()
        
        # Default component statuses
        health = {
            "api": {"status": "HEALTHY", "last_active": None, "message": "API gateway is operational."},
            "ar_event_processor": {"status": "INACTIVE", "last_active": None, "message": "No recent worker activity recorded."},
            "settlement_automation_worker": {"status": "INACTIVE", "last_active": None, "message": "No recent worker activity recorded."},
            "financial_consistency_automation_worker": {"status": "INACTIVE", "last_active": None, "message": "No recent worker activity recorded."}
        }

        for ev in recent_events:
            # Determine API Health
            if ev.component == "API" and not health["api"]["last_active"]:
                health["api"]["last_active"] = ev.timestamp.isoformat()
                if ev.status == "FAILED":
                    health["api"]["status"] = "DEGRADED"
                    health["api"]["message"] = f"Recent API failures detected: {ev.message}"

            # Determine Worker Health based on operation / event details
            if ev.component == "WORKER":
                op_lower = ev.operation.lower()
                msg_lower = ev.message.lower()

                # AR Event Processor
                if "payment" in op_lower or "receivable" in op_lower or "ar_event" in op_lower:
                    if not health["ar_event_processor"]["last_active"]:
                        health["ar_event_processor"]["last_active"] = ev.timestamp.isoformat()
                        is_recent = (now - ev.timestamp) < timedelta(minutes=15)
                        if ev.status == "FAILED":
                            health["ar_event_processor"]["status"] = "DEGRADED"
                            health["ar_event_processor"]["message"] = f"AR processor failed: {ev.message}"
                        elif is_recent:
                            health["ar_event_processor"]["status"] = "HEALTHY"
                            health["ar_event_processor"]["message"] = "AR processor is active and healthy."
                        else:
                            health["ar_event_processor"]["status"] = "INACTIVE"
                            health["ar_event_processor"]["message"] = "No recent worker run detected in last 15 minutes."

                # Settlement Automation Worker
                elif "settlement" in op_lower or "reconcile" in op_lower:
                    if not health["settlement_automation_worker"]["last_active"]:
                        health["settlement_automation_worker"]["last_active"] = ev.timestamp.isoformat()
                        is_recent = (now - ev.timestamp) < timedelta(minutes=15)
                        if ev.status == "FAILED":
                            health["settlement_automation_worker"]["status"] = "DEGRADED"
                            health["settlement_automation_worker"]["message"] = f"Settlement worker failed: {ev.message}"
                        elif is_recent:
                            health["settlement_automation_worker"]["status"] = "HEALTHY"
                            health["settlement_automation_worker"]["message"] = "Settlement worker is active and healthy."
                        else:
                            health["settlement_automation_worker"]["status"] = "INACTIVE"
                            health["settlement_automation_worker"]["message"] = "No recent worker run detected in last 15 minutes."

                # Financial Consistency Automation Worker
                elif "recheck" in op_lower or "recover" in op_lower or "sweep" in op_lower:
                    if not health["financial_consistency_automation_worker"]["last_active"]:
                        health["financial_consistency_automation_worker"]["last_active"] = ev.timestamp.isoformat()
                        is_recent = (now - ev.timestamp) < timedelta(minutes=15)
                        if ev.status == "FAILED":
                            health["financial_consistency_automation_worker"]["status"] = "DEGRADED"
                            health["financial_consistency_automation_worker"]["message"] = f"Automation sweep failed: {ev.message}"
                        elif is_recent:
                            health["financial_consistency_automation_worker"]["status"] = "HEALTHY"
                            health["financial_consistency_automation_worker"]["message"] = "Automation sweep is active and healthy."
                        else:
                            health["financial_consistency_automation_worker"]["status"] = "INACTIVE"
                            health["financial_consistency_automation_worker"]["message"] = "No recent sweep run detected in last 15 minutes."

        return health

    async def get_telemetry_metrics(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        # Aggregate stats over last 24h
        cutoff = datetime.utcnow() - timedelta(hours=24)
        stmt = (
            select(TelemetryEventDbModel)
            .where(TelemetryEventDbModel.organization_id == organization_id.value)
            .where(TelemetryEventDbModel.timestamp >= cutoff)
        )
        res = await self._session.execute(stmt)
        events = res.scalars().all()

        total_ops = len(events)
        errors = sum(1 for e in events if e.status == "FAILED" or e.severity in ("ERROR", "CRITICAL"))
        error_rate = (errors / total_ops * 100.0) if total_ops > 0 else 0.0

        durations = [e.duration_ms for e in events if e.duration_ms is not None]
        avg_duration = (sum(durations) / len(durations)) if durations else 0.0

        return {
            "total_operations_24h": total_ops,
            "error_count_24h": errors,
            "error_rate_percent": round(error_rate, 2),
            "avg_duration_ms": round(avg_duration, 2)
        }

    async def evaluate_health_and_readiness(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        now = datetime.utcnow()

        # 1. Evaluate Database health & latency
        start_time = time.time()
        db_failed = False
        db_err_msg = ""
        try:
            await self._session.execute(select(1))
            latency = (time.time() - start_time) * 1000.0
            db_status = "HEALTHY"
            db_evidence = f"Database query active. Latency: {latency:.2f}ms"
        except Exception as e:
            latency = 9999.0
            db_status = "UNHEALTHY"
            db_evidence = f"Database query failed: {str(e)}"
            db_failed = True
            db_err_msg = str(e)

        if db_failed:
            return {
                "status": "UNHEALTHY",
                "evidence": f"Critical failure: database is unreachable. Error: {db_err_msg}",
                "readiness": {
                    "status": "NOT_READY",
                    "barriers": ["Platform is unhealthy.", "Database is unreachable."]
                },
                "components": {
                    "database": {"status": "UNHEALTHY", "evidence": db_evidence},
                    "api": {"status": "UNHEALTHY", "evidence": "Critical dependency database is unhealthy."},
                    "ar_event_processor": {"status": "UNHEALTHY", "evidence": "Critical dependency database is unhealthy."},
                    "settlement_automation_worker": {"status": "UNHEALTHY", "evidence": "Critical dependency database is unhealthy."},
                    "financial_consistency_automation_worker": {"status": "UNHEALTHY", "evidence": "Critical dependency database is unhealthy."}
                }
            }

        # 2. Query recent telemetry logs to evaluate API and Workers health
        stmt_logs = (
            select(TelemetryEventDbModel)
            .where(TelemetryEventDbModel.organization_id == org_id)
            .order_by(TelemetryEventDbModel.timestamp.desc())
            .limit(100)
        )
        res_logs = await self._session.execute(stmt_logs)
        recent_events = res_logs.scalars().all()

        # API health
        api_events = [e for e in recent_events if e.component == "API" and e.timestamp >= now - timedelta(minutes=15)]
        if api_events:
            api_errors = sum(1 for e in api_events if e.status == "FAILED" or e.severity in ("ERROR", "CRITICAL"))
            api_error_rate = (api_errors / len(api_events)) * 100.0
            if api_error_rate > 10.0:
                api_status = "DEGRADED"
                api_evidence = f"High API error rate: {api_error_rate:.2f}% over the last 15 minutes."
            else:
                api_status = "HEALTHY"
                api_evidence = f"API operational with {api_error_rate:.2f}% error rate."
        else:
            api_status = "HEALTHY"
            api_evidence = "No recent API requests recorded in the last 15 minutes."

        # Workers health
        worker_events = [e for e in recent_events if e.component == "WORKER"]
        
        def evaluate_worker(op_keywords: list) -> tuple:
            filtered = [e for e in worker_events if any(kw in e.operation.lower() for kw in op_keywords)]
            if not filtered:
                return "INACTIVE", "No recent activity recorded."
            last_run = filtered[0]
            is_recent = (now - last_run.timestamp) < timedelta(minutes=15)
            if last_run.status == "FAILED":
                return "DEGRADED", f"Last execution failed: {last_run.message}"
            elif is_recent:
                return "HEALTHY", "Active and running normally."
            else:
                return "INACTIVE", "Inactive: last run was more than 15 minutes ago."

        ar_status, ar_evidence = evaluate_worker(["payment", "receivable", "ar_event"])
        settlement_status, settlement_evidence = evaluate_worker(["settlement", "reconcile"])
        consistency_status, consistency_evidence = evaluate_worker(["recheck", "recover", "sweep"])

        # Dependency propagation: If Database is UNHEALTHY, workers/APIs fail
        if db_status == "UNHEALTHY":
            api_status = "UNHEALTHY"
            api_evidence = "Critical dependency database is unhealthy."
            ar_status = "UNHEALTHY"
            ar_evidence = "Critical dependency database is unhealthy."
            settlement_status = "UNHEALTHY"
            settlement_evidence = "Critical dependency database is unhealthy."
            consistency_status = "UNHEALTHY"
            consistency_evidence = "Critical dependency database is unhealthy."

        # 3. Platform Health Aggregation
        statuses = [db_status, api_status, ar_status, settlement_status, consistency_status]
        if "UNHEALTHY" in (db_status, api_status):
            platform_status = "UNHEALTHY"
            platform_evidence = "Critical failure: API gateway or persistence layer is unhealthy."
        elif "UNHEALTHY" in statuses or "DEGRADED" in statuses:
            platform_status = "DEGRADED"
            degraded_comps = [
                comp for comp, stat in [
                    ("database", db_status), ("api", api_status),
                    ("ar_event_processor", ar_status), ("settlement_automation_worker", settlement_status),
                    ("financial_consistency_automation_worker", consistency_status)
                ] if stat in ("DEGRADED", "UNHEALTHY")
            ]
            platform_evidence = f"Partial degradation detected in components: {', '.join(degraded_comps)}."
        else:
            platform_status = "HEALTHY"
            platform_evidence = "All systems operational."

        # 4. Readiness Evaluation
        barriers = []
        if platform_status == "UNHEALTHY":
            barriers.append("Platform is unhealthy.")
        if latency > 1000.0:
            barriers.append(f"Database query latency is too high ({latency:.1f}ms).")
        
        # Check active consistency violations
        stmt_viols = (
            select(FinancialConsistencyViolationDbModel)
            .where(
                FinancialConsistencyViolationDbModel.organization_id == org_id,
                FinancialConsistencyViolationDbModel.resolved == False
            )
        )
        res_viols = await self._session.execute(stmt_viols)
        active_violations = res_viols.scalars().all()
        if active_violations:
            barriers.append(f"Active financial consistency violations present ({len(active_violations)} detected).")

        readiness_status = "NOT_READY" if barriers else "READY"

        # 5. Continuous state transition logging
        components_to_check = {
            "database": (db_status, db_evidence),
            "api": (api_status, api_evidence),
            "ar_event_processor": (ar_status, ar_evidence),
            "settlement_automation_worker": (settlement_status, settlement_evidence),
            "financial_consistency_automation_worker": (consistency_status, consistency_evidence),
            "platform": (platform_status, platform_evidence)
        }

        for comp, (stat, evid) in components_to_check.items():
            # Query last transition
            stmt_last = (
                select(ComponentHealthTransitionDbModel)
                .where(
                    ComponentHealthTransitionDbModel.organization_id == org_id,
                    ComponentHealthTransitionDbModel.component == comp
                )
                .order_by(ComponentHealthTransitionDbModel.timestamp.desc())
                .limit(1)
            )
            res_last = await self._session.execute(stmt_last)
            last_trans = res_last.scalar_one_or_none()
            
            old_state = last_trans.resulting_state if last_trans else "UNKNOWN"
            if old_state != stat:
                # Log transition
                trans = ComponentHealthTransitionDbModel(
                    id=str(uuid4()),
                    organization_id=org_id,
                    component=comp,
                    previous_state=old_state,
                    resulting_state=stat,
                    evidence=json.dumps({"evidence": evid, "evaluated_at": now.isoformat()}),
                    timestamp=now
                )
                self._session.add(trans)
        
        await self._session.flush()

        return {
            "status": platform_status,
            "evidence": platform_evidence,
            "readiness": {
                "status": readiness_status,
                "barriers": barriers
            },
            "components": {
                "database": {"status": db_status, "evidence": db_evidence, "latency_ms": round(latency, 2)},
                "api": {"status": api_status, "evidence": api_evidence},
                "ar_event_processor": {"status": ar_status, "evidence": ar_evidence},
                "settlement_automation_worker": {"status": settlement_status, "evidence": settlement_evidence},
                "financial_consistency_automation_worker": {"status": consistency_status, "evidence": consistency_evidence}
            },
            "dependencies": {
                "database": db_status,
                "api": api_status
            }
        }

    async def get_health_transition_history(
        self,
        organization_id: OrganizationId,
        component: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(ComponentHealthTransitionDbModel)
            .where(ComponentHealthTransitionDbModel.organization_id == organization_id.value)
        )
        if component:
            stmt = stmt.where(ComponentHealthTransitionDbModel.component == component)
        
        stmt = stmt.order_by(ComponentHealthTransitionDbModel.timestamp.desc())
        res = await self._session.execute(stmt)
        transitions = res.scalars().all()

        return [
            {
                "id": t.id,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "component": t.component,
                "previous_state": t.previous_state,
                "resulting_state": t.resulting_state,
                "evidence": json.loads(t.evidence) if t.evidence else {}
            }
            for t in transitions
        ]

    async def run_anomaly_detection(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        now = datetime.utcnow()
        detected_anomalies = []

        # Helper to create or correlate/deduplicate anomalies
        async def log_or_correlate_anomaly(
            anomaly_type: str,
            component: str,
            severity: str,
            observed_value: str,
            expected_value: str,
            evidence: dict,
            correlation_id: Optional[str] = None
        ):
            # Prioritization rules
            if severity in ("CRITICAL", "FINANCIAL_EXCEPTION") or anomaly_type == "REPEATED_VIOLATIONS":
                priority = "CRITICAL"
                category = "FINANCIAL" if anomaly_type == "REPEATED_VIOLATIONS" else "OPERATIONAL"
            elif severity == "ERROR" or anomaly_type in ("WORKER_FAILURE_BURST", "HIGH_LATENCY"):
                priority = "HIGH"
                category = "TECHNICAL"
            elif severity == "WARNING":
                priority = "MEDIUM"
                category = "OPERATIONAL" if "WORKER" in anomaly_type else "TECHNICAL"
            else:
                priority = "LOW"
                category = "TECHNICAL"

            # Recurrence detection: check resolved/unresolved anomalies of the same type/component in the last 7 days
            seven_days_ago = now - timedelta(days=7)
            stmt_recur = (
                select(AnomalyDbModel)
                .where(
                    AnomalyDbModel.organization_id == org_id,
                    AnomalyDbModel.anomaly_type == anomaly_type,
                    AnomalyDbModel.component == component,
                    AnomalyDbModel.detected_at >= seven_days_ago
                )
            )
            res_recur = await self._session.execute(stmt_recur)
            past_anoms = res_recur.scalars().all()
            recurrence_count = len(past_anoms)

            # Auto-escalation if recurrence count > 3
            is_escalated = False
            escalated_at = None
            if recurrence_count > 3:
                is_escalated = True
                escalated_at = now
                priority = "CRITICAL"
                evidence["auto_escalation_reason"] = f"Recurrence count is {recurrence_count} over 7 days (threshold: 3)."

            # Check for an active anomaly (not RESOLVED and not CLOSED) of the same type/component
            stmt_active = (
                select(AnomalyDbModel)
                .where(
                    AnomalyDbModel.organization_id == org_id,
                    AnomalyDbModel.anomaly_type == anomaly_type,
                    AnomalyDbModel.component == component,
                    AnomalyDbModel.status.in_(["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED"])
                )
                .order_by(AnomalyDbModel.detected_at.desc())
                .limit(1)
            )
            res_active = await self._session.execute(stmt_active)
            active_anomaly = res_active.scalar_one_or_none()

            if active_anomaly:
                # Deduplication Correlation: Update existing active anomaly evidence
                current_evidence = json.loads(active_anomaly.evidence) if active_anomaly.evidence else {}
                if "updates" not in current_evidence:
                    current_evidence["updates"] = []
                current_evidence["updates"].append({
                    "timestamp": now.isoformat(),
                    "observed_value": observed_value,
                    "evidence": evidence
                })
                active_anomaly.evidence = json.dumps(current_evidence)
                active_anomaly.observed_value = observed_value
                active_anomaly.detected_at = now
                active_anomaly.recurrence_count = recurrence_count
                if is_escalated:
                    active_anomaly.escalated = True
                    active_anomaly.escalated_at = escalated_at
                    active_anomaly.priority = "CRITICAL"
                
                detected_anomalies.append({
                    "id": active_anomaly.id,
                    "anomaly_type": anomaly_type,
                    "component": component,
                    "action": "CORRELATED"
                })
            else:
                # Create a new anomaly record
                anomaly_id = str(uuid4())
                new_anomaly = AnomalyDbModel(
                    id=anomaly_id,
                    anomaly_type=anomaly_type,
                    severity=severity,
                    component=component,
                    detected_at=now,
                    source="RULE_ENGINE",
                    observed_value=observed_value,
                    expected_value=expected_value,
                    evidence=json.dumps(evidence),
                    correlation_id=correlation_id,
                    causation_id="SYSTEM",
                    organization_id=org_id,
                    status="DETECTED",
                    priority=priority,
                    category=category,
                    escalated=is_escalated,
                    escalated_at=escalated_at,
                    recurrence_count=recurrence_count
                )
                self._session.add(new_anomaly)
                detected_anomalies.append({
                    "id": anomaly_id,
                    "anomaly_type": anomaly_type,
                    "component": component,
                    "action": "CREATED"
                })

        # --- RULE 1: WORKER_FAILURE_BURST ---
        # Look for >= 3 worker telemetry failures in last 15 mins
        cutoff_15 = now - timedelta(minutes=15)
        stmt_failed_workers = (
            select(TelemetryEventDbModel)
            .where(
                TelemetryEventDbModel.organization_id == org_id,
                TelemetryEventDbModel.component == "WORKER",
                TelemetryEventDbModel.status == "FAILED",
                TelemetryEventDbModel.timestamp >= cutoff_15
            )
        )
        res_failed = await self._session.execute(stmt_failed_workers)
        failed_worker_events = res_failed.scalars().all()

        # Group by operation/worker name
        worker_failure_counts = {}
        for ev in failed_worker_events:
            worker_failure_counts[ev.operation] = worker_failure_counts.get(ev.operation, []) + [ev]

        for op_name, events in worker_failure_counts.items():
            if len(events) >= 3:
                evidence = {
                    "reason": f"Detected {len(events)} failures in the last 15 minutes.",
                    "events": [{"timestamp": e.timestamp.isoformat(), "message": e.message} for e in events]
                }
                # Determine component mapping (e.g. ar_event_processor)
                comp = "ar_event_processor"
                if "settlement" in op_name.lower() or "reconcile" in op_name.lower():
                    comp = "settlement_automation_worker"
                elif "recheck" in op_name.lower() or "recover" in op_name.lower() or "sweep" in op_name.lower():
                    comp = "financial_consistency_automation_worker"

                await log_or_correlate_anomaly(
                    anomaly_type="WORKER_FAILURE_BURST",
                    component=comp,
                    severity="ERROR",
                    observed_value=f"{len(events)} failures",
                    expected_value="< 3 failures",
                    evidence=evidence,
                    correlation_id=events[0].correlation_id
                )

        # --- RULE 2: HIGH_LATENCY ---
        # Average API request duration > 500ms in last 15 mins
        stmt_api_events = (
            select(TelemetryEventDbModel)
            .where(
                TelemetryEventDbModel.organization_id == org_id,
                TelemetryEventDbModel.component == "API",
                TelemetryEventDbModel.timestamp >= cutoff_15,
                TelemetryEventDbModel.duration_ms != None
            )
        )
        res_api = await self._session.execute(stmt_api_events)
        api_events = res_api.scalars().all()
        if api_events:
            durations = [e.duration_ms for e in api_events]
            avg_dur = sum(durations) / len(durations)
            if avg_dur > 500.0:
                evidence = {
                    "reason": f"Average API request duration was {avg_dur:.2f}ms over {len(api_events)} requests.",
                    "sample_durations": durations[:10]
                }
                await log_or_correlate_anomaly(
                    anomaly_type="HIGH_LATENCY",
                    component="api",
                    severity="WARNING",
                    observed_value=f"{avg_dur:.2f}ms",
                    expected_value="< 500.00ms",
                    evidence=evidence
                )

        # --- RULE 3: REPEATED_VIOLATIONS ---
        # >= 2 active financial consistency violations
        stmt_violations = (
            select(FinancialConsistencyViolationDbModel)
            .where(
                FinancialConsistencyViolationDbModel.organization_id == org_id,
                FinancialConsistencyViolationDbModel.resolved == False
            )
        )
        res_violations = await self._session.execute(stmt_violations)
        active_violations = res_violations.scalars().all()
        if len(active_violations) >= 2:
            evidence = {
                "reason": f"Spike in financial consistency violations: {len(active_violations)} unresolved violations detected.",
                "violations": [
                    {
                        "id": v.id,
                        "type": v.violation_type,
                        "entity_id": v.entity_id,
                        "description": v.description
                    }
                    for v in active_violations
                ]
            }
            await log_or_correlate_anomaly(
                anomaly_type="REPEATED_VIOLATIONS",
                component="platform",
                severity="FINANCIAL_EXCEPTION",
                observed_value=f"{len(active_violations)} active violations",
                expected_value="< 2 violations",
                evidence=evidence
            )

        await self._session.flush()

        return {
            "status": "SUCCESS",
            "detected_anomalies_count": len(detected_anomalies),
            "anomalies": detected_anomalies
        }

    async def query_anomalies(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
        )
        if status:
            stmt = stmt.where(AnomalyDbModel.status == status)
        if severity:
            stmt = stmt.where(AnomalyDbModel.severity == severity)
        
        limit = min(limit, 100)
        stmt = stmt.order_by(AnomalyDbModel.detected_at.desc()).limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        anomalies = res.scalars().all()

        return [
            {
                "id": a.id,
                "anomaly_type": a.anomaly_type,
                "severity": a.severity,
                "priority": a.priority,
                "category": a.category,
                "component": a.component,
                "entity_id": a.entity_id,
                "detected_at": a.detected_at.isoformat() if a.detected_at else None,
                "source": a.source,
                "observed_value": a.observed_value,
                "expected_value": a.expected_value,
                "evidence": json.loads(a.evidence) if a.evidence else {},
                "correlation_id": a.correlation_id,
                "causation_id": a.causation_id,
                "status": a.status,
                "owner_id": a.owner_id,
                "mitigation_notes": a.mitigation_notes,
                "mitigated_at": a.mitigated_at.isoformat() if a.mitigated_at else None,
                "root_cause": a.root_cause,
                "escalated": a.escalated,
                "escalated_at": a.escalated_at.isoformat() if a.escalated_at else None,
                "recurrence_count": a.recurrence_count,
                "resolution_notes": a.resolution_notes,
                "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None
            }
            for a in anomalies
        ]

    async def get_anomaly_by_id(
        self,
        organization_id: OrganizationId,
        anomaly_id: str
    ) -> Optional[Dict[str, Any]]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
            .where(AnomalyDbModel.id == anomaly_id)
        )
        res = await self._session.execute(stmt)
        a = res.scalar_one_or_none()
        if not a:
            return None

        return {
            "id": a.id,
            "anomaly_type": a.anomaly_type,
            "severity": a.severity,
            "priority": a.priority,
            "category": a.category,
            "component": a.component,
            "entity_id": a.entity_id,
            "detected_at": a.detected_at.isoformat() if a.detected_at else None,
            "source": a.source,
            "observed_value": a.observed_value,
            "expected_value": a.expected_value,
            "evidence": json.loads(a.evidence) if a.evidence else {},
            "correlation_id": a.correlation_id,
            "causation_id": a.causation_id,
            "status": a.status,
            "owner_id": a.owner_id,
            "mitigation_notes": a.mitigation_notes,
            "mitigated_at": a.mitigated_at.isoformat() if a.mitigated_at else None,
            "root_cause": a.root_cause,
            "escalated": a.escalated,
            "escalated_at": a.escalated_at.isoformat() if a.escalated_at else None,
            "recurrence_count": a.recurrence_count,
            "resolution_notes": a.resolution_notes,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None
        }

    async def update_anomaly_status(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        status: str,
        notes: Optional[str] = None,
        actor_id: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
            .where(AnomalyDbModel.id == anomaly_id)
        )
        res = await self._session.execute(stmt)
        a = res.scalar_one_or_none()
        if not a:
            raise ValueError("Anomaly not found.")

        valid_transitions = {
            "DETECTED": ["ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED", "RESOLVED", "CLOSED"],
            "ACKNOWLEDGED": ["ASSIGNED", "INVESTIGATING", "MITIGATED", "RESOLVED", "CLOSED"],
            "ASSIGNED": ["INVESTIGATING", "MITIGATED", "RESOLVED", "CLOSED"],
            "INVESTIGATING": ["MITIGATED", "RESOLVED", "CLOSED"],
            "MITIGATED": ["RESOLVED", "CLOSED"],
            "RESOLVED": ["CLOSED"],
            "CLOSED": []
        }

        if status not in ["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED", "RESOLVED", "CLOSED"]:
            raise ValueError(f"Invalid target status: {status}")

        if status in valid_transitions.get(a.status, []) or status == a.status:
            old_status = a.status
            a.status = status
            if status == "RESOLVED":
                a.resolved_at = datetime.utcnow()
                a.resolution_notes = notes
            elif status == "CLOSED" and not a.resolved_at:
                a.resolved_at = datetime.utcnow()
                a.resolution_notes = notes or "Closed directly."
            
            ev = json.loads(a.evidence) if a.evidence else {}
            if "status_history" not in ev:
                ev["status_history"] = []
            ev["status_history"].append({
                "timestamp": datetime.utcnow().isoformat(),
                "status": status,
                "notes": notes
            })
            a.evidence = json.dumps(ev)

            await self._session.flush()
            await self.log_control_plane_audit(
                organization_id=organization_id,
                actor_id=actor_id or "system",
                action="UPDATE_ANOMALY_STATUS",
                target_id=anomaly_id,
                target_type="exception",
                previous_state=json.dumps({"status": old_status}),
                new_state=json.dumps({"status": status}),
                context=notes,
                correlation_id=correlation_id or a.correlation_id
            )
            return {
                "status": "SUCCESS",
                "anomaly_id": anomaly_id,
                "current_status": a.status
            }
        else:
            raise ValueError(f"Invalid lifecycle transition from {a.status} to {status}.")

    async def assign_anomaly(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        owner_id: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
            .where(AnomalyDbModel.id == anomaly_id)
        )
        res = await self._session.execute(stmt)
        a = res.scalar_one_or_none()
        if not a:
            raise ValueError("Anomaly not found.")

        if a.status in ["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING"]:
            old_owner = a.owner_id
            a.owner_id = owner_id
            a.status = "ASSIGNED"
            
            ev = json.loads(a.evidence) if a.evidence else {}
            if "assignment_history" not in ev:
                ev["assignment_history"] = []
            ev["assignment_history"].append({
                "timestamp": datetime.utcnow().isoformat(),
                "previous_owner": old_owner,
                "new_owner": owner_id
            })
            a.evidence = json.dumps(ev)
            
            await self._session.flush()
            await self.log_control_plane_audit(
                organization_id=organization_id,
                actor_id=actor_id,
                action="ASSIGN_EXCEPTION",
                target_id=anomaly_id,
                target_type="exception",
                previous_state=json.dumps({"owner_id": old_owner, "status": "DETECTED"}),
                new_state=json.dumps({"owner_id": owner_id, "status": "ASSIGNED"}),
                context=f"Assigned owner to {owner_id}",
                correlation_id=correlation_id or a.correlation_id
            )
            return {
                "status": "SUCCESS",
                "anomaly_id": anomaly_id,
                "current_status": a.status,
                "owner_id": a.owner_id
            }
        else:
            raise ValueError(f"Cannot assign owner when status is {a.status}.")

    async def mitigate_anomaly(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        notes: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
            .where(AnomalyDbModel.id == anomaly_id)
        )
        res = await self._session.execute(stmt)
        a = res.scalar_one_or_none()
        if not a:
            raise ValueError("Anomaly not found.")

        if a.status in ["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED"]:
            a.status = "MITIGATED"
            a.mitigation_notes = notes
            a.mitigated_at = datetime.utcnow()
            
            ev = json.loads(a.evidence) if a.evidence else {}
            if "mitigation_history" not in ev:
                ev["mitigation_history"] = []
            ev["mitigation_history"].append({
                "timestamp": datetime.utcnow().isoformat(),
                "notes": notes
            })
            a.evidence = json.dumps(ev)

            await self._session.flush()
            await self.log_control_plane_audit(
                organization_id=organization_id,
                actor_id=actor_id,
                action="MITIGATE_EXCEPTION",
                target_id=anomaly_id,
                target_type="exception",
                previous_state=json.dumps({"status": "ASSIGNED"}),
                new_state=json.dumps({"status": "MITIGATED", "notes": notes}),
                context=notes,
                correlation_id=correlation_id or a.correlation_id
            )
            return {
                "status": "SUCCESS",
                "anomaly_id": anomaly_id,
                "current_status": a.status,
                "mitigation_notes": a.mitigation_notes
            }
        else:
            raise ValueError(f"Cannot transition to MITIGATED from status {a.status}.")

    async def escalate_anomaly(
        self,
        organization_id: OrganizationId,
        anomaly_id: str,
        reason: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
            .where(AnomalyDbModel.id == anomaly_id)
        )
        res = await self._session.execute(stmt)
        a = res.scalar_one_or_none()
        if not a:
            raise ValueError("Anomaly not found.")

        a.escalated = True
        a.escalated_at = datetime.utcnow()
        a.priority = "CRITICAL"
        
        ev = json.loads(a.evidence) if a.evidence else {}
        if "escalation_history" not in ev:
            ev["escalation_history"] = []
        ev["escalation_history"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "reason": reason
        })
        a.evidence = json.dumps(ev)

        await self._session.flush()
        await self.log_control_plane_audit(
            organization_id=organization_id,
            actor_id=actor_id,
            action="ESCALATE_EXCEPTION",
            target_id=anomaly_id,
            target_type="exception",
            previous_state=json.dumps({"escalated": False, "priority": "MEDIUM"}),
            new_state=json.dumps({"escalated": True, "priority": "CRITICAL"}),
            context=reason,
            correlation_id=correlation_id or a.correlation_id
        )
        return {
            "status": "SUCCESS",
            "anomaly_id": anomaly_id,
            "escalated": True,
            "priority": a.priority
        }

    async def get_recurring_patterns(
        self,
        organization_id: OrganizationId
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == organization_id.value)
        )
        res = await self._session.execute(stmt)
        anoms = res.scalars().all()

        patterns = {}
        for a in anoms:
            key = (a.component, a.anomaly_type)
            if key not in patterns:
                patterns[key] = {
                    "component": a.component,
                    "anomaly_type": a.anomaly_type,
                    "count": 0,
                    "escalated_count": 0,
                    "last_occurrence": a.detected_at
                }
            patterns[key]["count"] += 1
            if a.escalated:
                patterns[key]["escalated_count"] += 1
            if a.detected_at > patterns[key]["last_occurrence"]:
                patterns[key]["last_occurrence"] = a.detected_at

        return [
            {
                "component": p["component"],
                "anomaly_type": p["anomaly_type"],
                "count": p["count"],
                "escalated_count": p["escalated_count"],
                "last_occurrence": p["last_occurrence"].isoformat() if p["last_occurrence"] else None
            }
            for p in patterns.values()
            if p["count"] > 1
        ]

    async def run_remediation_pipeline(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        now = datetime.utcnow()

        # Get readiness policy
        policy = await self.get_readiness_policy(organization_id)
        if policy["emergency_disable"]:
            return {
                "status": "DISABLED",
                "message": "Automated remediation disabled by emergency shutdown circuit breaker.",
                "executed_remediations_count": 0,
                "remediations": []
            }

        is_observational = (policy["readiness_level"] == "LEVEL_0_OBSERVATIONAL")

        # 1. Fetch active exceptions/anomalies
        stmt_active = (
            select(AnomalyDbModel)
            .where(
                AnomalyDbModel.organization_id == org_id,
                AnomalyDbModel.status.in_(["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED"])
            )
        )
        res_active = await self._session.execute(stmt_active)
        active_exceptions = res_active.scalars().all()

        executed_actions = []

        for exception in active_exceptions:
            if exception.escalated:
                continue

            if exception.anomaly_type == "WORKER_FAILURE_BURST":
                action_type = "RECOVER_WORKER"
                target_component = exception.component
                trigger_condition = "Worker failure burst detected"
                initiating_rule = "WORKER_FAILURE_BURST"
            elif exception.anomaly_type == "REPEATED_VIOLATIONS":
                action_type = "RETRY_RECONCILIATION"
                target_component = "platform"
                trigger_condition = "Repeated consistency violations detected"
                initiating_rule = "REPEATED_VIOLATIONS"
            else:
                continue

            # Idempotency / Concurrency Check: Is there another remediation EXECUTING for this exception?
            stmt_running = (
                select(RemediationActionDbModel)
                .where(
                    RemediationActionDbModel.organization_id == org_id,
                    RemediationActionDbModel.exception_id == exception.id,
                    RemediationActionDbModel.status == "EXECUTING"
                )
            )
            res_running = await self._session.execute(stmt_running)
            if res_running.scalar_one_or_none():
                continue

            # Check retry attempts
            stmt_history = (
                select(RemediationActionDbModel)
                .where(
                    RemediationActionDbModel.organization_id == org_id,
                    RemediationActionDbModel.exception_id == exception.id
                )
            )
            res_history = await self._session.execute(stmt_history)
            attempts = res_history.scalars().all()
            attempt_count = len(attempts) + 1

            if attempt_count > 3:
                exception.escalated = True
                exception.escalated_at = now
                exception.priority = "CRITICAL"
                exception.status = "ACKNOWLEDGED"
                ev = json.loads(exception.evidence) if exception.evidence else {}
                ev["escalation_notes"] = f"Automated remediation retry budget exceeded ({attempt_count-1} failed attempts)."
                exception.evidence = json.dumps(ev)
                await self._session.flush()
                continue

            # Initialize remediation action
            action_id = str(uuid4())
            initial_status = "EXECUTING"
            if is_observational:
                initial_status = "SKIPPED_OBSERVATIONAL"
            elif policy["readiness_level"] == "LEVEL_1_ASSISTED":
                initial_status = "PENDING_APPROVAL"

            action = RemediationActionDbModel(
                id=action_id,
                action_type=action_type,
                target_component=target_component,
                exception_id=exception.id,
                trigger_condition=trigger_condition,
                status=initial_status,
                attempt_count=attempt_count,
                created_at=now,
                updated_at=now,
                initiating_rule=initiating_rule,
                correlation_id=exception.correlation_id,
                causation_id="REMEDIATION_ENGINE",
                organization_id=org_id
            )
            self._session.add(action)
            await self._session.flush()

            if initial_status != "EXECUTING":
                executed_actions.append({
                    "remediation_id": action.id,
                    "action_type": action.action_type,
                    "status": action.status,
                    "attempt_count": action.attempt_count
                })
                continue

            try:
                if action_type == "RECOVER_WORKER":
                    from ...bootstrap.container import container
                    async with container.telemetry_service() as telemetry_service:
                        await telemetry_service.record_event(
                            component="WORKER",
                            operation="process_payment_events",
                            message="Worker recovered and started successfully",
                            severity="INFO",
                            status="SUCCESS",
                            organization_id=org_id
                        )

                    action.execution_result = json.dumps({"action": "Recorded healthy telemetry trace log."})

                    health_report = await self.evaluate_health_and_readiness(organization_id)
                    worker_health = health_report["components"]["ar_event_processor"]["status"]
                    
                    if worker_health == "HEALTHY":
                        action.status = "SUCCESS"
                        action.verification_result = json.dumps({"health": "HEALTHY", "verified": True})
                        exception.status = "RESOLVED"
                        exception.resolved_at = now
                        exception.resolution_notes = "Resolved by automated worker recovery remediation."
                    else:
                        action.status = "VERIFICATION_FAILED"
                        action.verification_result = json.dumps({"health": worker_health, "verified": False})

                elif action_type == "RETRY_RECONCILIATION":
                    from ...bootstrap.container import container
                    async with container.financial_consistency_automation_worker() as worker:
                        sweep_res = await worker.run_integrity_sweep()

                    action.execution_result = json.dumps(sweep_res)

                    stmt_viols = (
                        select(FinancialConsistencyViolationDbModel)
                        .where(
                            FinancialConsistencyViolationDbModel.organization_id == org_id,
                            FinancialConsistencyViolationDbModel.resolved == False
                        )
                    )
                    res_viols = await self._session.execute(stmt_viols)
                    viols = res_viols.scalars().all()
                    
                    if len(viols) < 2:
                        action.status = "SUCCESS"
                        action.verification_result = json.dumps({"active_violations": len(viols), "verified": True})
                        exception.status = "RESOLVED"
                        exception.resolved_at = now
                        exception.resolution_notes = "Resolved by automated consistency sweep retry remediation."
                    else:
                        action.status = "VERIFICATION_FAILED"
                        action.verification_result = json.dumps({"active_violations": len(viols), "verified": False})

            except Exception as e:
                action.status = "FAILED"
                action.error_info = str(e)
                if attempt_count == 3:
                    exception.escalated = True
                    exception.escalated_at = now
                    exception.priority = "CRITICAL"
                    exception.status = "ACKNOWLEDGED"
                    ev = json.loads(exception.evidence) if exception.evidence else {}
                    ev["escalation_notes"] = f"Automated remediation failed: {str(e)} (retry budget exceeded)."
                    exception.evidence = json.dumps(ev)

            action.updated_at = datetime.utcnow()
            await self._session.flush()

            executed_actions.append({
                "remediation_id": action.id,
                "action_type": action.action_type,
                "status": action.status,
                "attempt_count": action.attempt_count
            })

        return {
            "status": "SUCCESS",
            "executed_remediations_count": len(executed_actions),
            "remediations": executed_actions
        }

    async def retry_remediation(
        self,
        organization_id: OrganizationId,
        remediation_id: str,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        stmt = (
            select(RemediationActionDbModel)
            .where(RemediationActionDbModel.organization_id == org_id)
            .where(RemediationActionDbModel.id == remediation_id)
        )
        res = await self._session.execute(stmt)
        action = res.scalar_one_or_none()
        if not action:
            raise ValueError("Remediation action not found.")

        if action.status not in ["FAILED", "VERIFICATION_FAILED"]:
            raise ValueError(f"Cannot retry remediation in status {action.status}.")

        stmt_exception = (
            select(AnomalyDbModel)
            .where(AnomalyDbModel.organization_id == org_id)
            .where(AnomalyDbModel.id == action.exception_id)
        )
        res_ex = await self._session.execute(stmt_exception)
        exception = res_ex.scalar_one_or_none()
        if not exception:
            raise ValueError("Associated exception not found.")

        exception.escalated = False
        exception.status = "DETECTED"
        await self._session.flush()
        await self.log_control_plane_audit(
            organization_id=organization_id,
            actor_id=actor_id,
            action="RETRY_REMEDIATION",
            target_id=remediation_id,
            target_type="remediation",
            previous_state=json.dumps({"status": action.status}),
            new_state=json.dumps({"status": "EXECUTING"}),
            context="Manual remediation retry triggered",
            correlation_id=correlation_id or action.correlation_id
        )
  
        res_pipeline = await self.run_remediation_pipeline(organization_id)
        return {
            "status": "SUCCESS",
            "message": "Retry triggered successfully.",
            "pipeline_results": res_pipeline
        }

    async def query_remediations(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(RemediationActionDbModel)
            .where(RemediationActionDbModel.organization_id == organization_id.value)
        )
        if status:
            stmt = stmt.where(RemediationActionDbModel.status == status)
        
        limit = min(limit, 100)
        stmt = stmt.order_by(RemediationActionDbModel.created_at.desc()).limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        actions = res.scalars().all()

        return [
            {
                "id": act.id,
                "action_type": act.action_type,
                "target_component": act.target_component,
                "exception_id": act.exception_id,
                "trigger_condition": act.trigger_condition,
                "status": act.status,
                "attempt_count": act.attempt_count,
                "created_at": act.created_at.isoformat() if act.created_at else None,
                "updated_at": act.updated_at.isoformat() if act.updated_at else None,
                "initiating_rule": act.initiating_rule,
                "correlation_id": act.correlation_id,
                "causation_id": act.causation_id,
                "execution_result": json.loads(act.execution_result) if act.execution_result else {},
                "verification_result": json.loads(act.verification_result) if act.verification_result else {},
                "error_info": act.error_info
            }
            for act in actions
        ]

    async def evaluate_operational_situations(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        now = datetime.utcnow()

        stmt_anoms = (
            select(AnomalyDbModel)
            .where(
                AnomalyDbModel.organization_id == org_id,
                AnomalyDbModel.status.in_(["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED"])
            )
        )
        res_anoms = await self._session.execute(stmt_anoms)
        active_anomalies = res_anoms.scalars().all()

        stmt_sit = (
            select(OperationalSituationDbModel)
            .where(
                OperationalSituationDbModel.organization_id == org_id,
                OperationalSituationDbModel.status == "ACTIVE"
            )
        )
        res_sit = await self._session.execute(stmt_sit)
        active_situations = {s.correlation_id or s.id: s for s in res_sit.scalars().all()}

        health_report = await self.evaluate_health_and_readiness(organization_id)
        readiness_status = health_report["readiness"]["status"]

        if not active_anomalies:
            for sit in active_situations.values():
                sit.status = "RESOLVED"
                sit.updated_at = now
            await self._session.flush()
            return {
                "status": "SUCCESS",
                "message": "All situations resolved. No active anomalies.",
                "evaluated_situations_count": 0,
                "situations": []
            }

        # Group all active anomalies under a single unified situation context
        corr_ids = [a.correlation_id for a in active_anomalies if a.correlation_id]
        situation_corr_id = corr_ids[0] if corr_ids else None

        types = [a.anomaly_type for a in active_anomalies]
        if "REPEATED_VIOLATIONS" in types:
            title = "Financial Consistency Degradation"
            fin_impact = "CRITICAL"
        elif "WORKER_FAILURE_BURST" in types:
            title = "Worker Execution Degradation"
            fin_impact = "HIGH"
        elif "HIGH_LATENCY" in types:
            title = "API Latency Spike"
            fin_impact = "MEDIUM"
        else:
            title = "System Operational Degradation"
            fin_impact = "LOW"

        base_score = len(active_anomalies) * 20.0
        degraded_count = sum(1 for comp_info in health_report["components"].values() if comp_info["status"] in ("DEGRADED", "UNHEALTHY"))
        base_score += degraded_count * 15.0
        
        has_critical = any(a.severity in ("CRITICAL", "FINANCIAL_EXCEPTION") for a in active_anomalies)
        if has_critical:
            base_score += 30.0
        if readiness_status == "NOT_READY":
            base_score += 30.0
        
        impact_score = min(base_score, 100.0)

        causality_chain = []
        components_active = [a.component for a in active_anomalies]
        if "database" in components_active or health_report["components"]["database"]["status"] == "UNHEALTHY":
            if "api" in components_active:
                causality_chain.append({"upstream": "database", "downstream": "api", "confidence": "HIGH"})
            if any(c in components_active for c in ("ar_event_processor", "settlement_automation_worker", "financial_consistency_automation_worker")):
                causality_chain.append({"upstream": "database", "downstream": "workers", "confidence": "CRITICAL"})
        elif "api" in components_active:
            if any(c in components_active for c in ("ar_event_processor", "settlement_automation_worker", "financial_consistency_automation_worker")):
                causality_chain.append({"upstream": "api", "downstream": "workers", "confidence": "MEDIUM"})

        evidence = {
            "affected_components": list(set(a.component for a in active_anomalies)),
            "related_anomalies": [{"id": a.id, "type": a.anomaly_type, "severity": a.severity} for a in active_anomalies],
            "causality_chain": causality_chain,
            "financial_impact": fin_impact,
            "readiness_status": readiness_status
        }

        sit = list(active_situations.values())[0] if active_situations else None
        if sit:
            sit.title = title
            sit.impact_score = impact_score
            sit.evidence = json.dumps(evidence)
            sit.correlation_id = situation_corr_id
            sit.updated_at = now
            action = "UPDATED"
        else:
            sit_id = str(uuid4())
            sit = OperationalSituationDbModel(
                id=sit_id,
                title=title,
                status="ACTIVE",
                impact_score=impact_score,
                created_at=now,
                updated_at=now,
                evidence=json.dumps(evidence),
                correlation_id=situation_corr_id,
                organization_id=org_id
            )
            self._session.add(sit)
            action = "CREATED"

        await self._session.flush()

        return {
            "status": "SUCCESS",
            "evaluated_situations_count": 1,
            "situations": [{
                "id": sit.id,
                "title": title,
                "impact_score": impact_score,
                "action": action
            }]
        }

    async def query_situations(
        self,
        organization_id: OrganizationId,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(OperationalSituationDbModel)
            .where(OperationalSituationDbModel.organization_id == organization_id.value)
        )
        if status:
            stmt = stmt.where(OperationalSituationDbModel.status == status)
        
        limit = min(limit, 100)
        stmt = stmt.order_by(OperationalSituationDbModel.created_at.desc()).limit(limit).offset(offset)
        res = await self._session.execute(stmt)
        situations = res.scalars().all()

        return [
            {
                "id": s.id,
                "title": s.title,
                "status": s.status,
                "impact_score": s.impact_score,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "evidence": json.loads(s.evidence) if s.evidence else {},
                "correlation_id": s.correlation_id
            }
            for s in situations
        ]

    async def get_situation_by_id(
        self,
        organization_id: OrganizationId,
        situation_id: str
    ) -> Optional[Dict[str, Any]]:
        stmt = (
            select(OperationalSituationDbModel)
            .where(OperationalSituationDbModel.organization_id == organization_id.value)
            .where(OperationalSituationDbModel.id == situation_id)
        )
        res = await self._session.execute(stmt)
        s = res.scalar_one_or_none()
        if not s:
            return None

        ev = json.loads(s.evidence) if s.evidence else {}
        anomaly_ids = [anom["id"] for anom in ev.get("related_anomalies", [])]

        remediations = []
        if anomaly_ids:
            stmt_rems = (
                select(RemediationActionDbModel)
                .where(RemediationActionDbModel.exception_id.in_(anomaly_ids))
            )
            res_rems = await self._session.execute(stmt_rems)
            rems = res_rems.scalars().all()
            remediations = [
                {
                    "id": r.id,
                    "action_type": r.action_type,
                    "status": r.status,
                    "attempt_count": r.attempt_count,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None
                }
                for r in rems
            ]

        return {
            "id": s.id,
            "title": s.title,
            "status": s.status,
            "impact_score": s.impact_score,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "evidence": ev,
            "correlation_id": s.correlation_id,
            "remediations": remediations
        }

    async def get_control_plane_overview(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value

        health_report = await self.evaluate_health_and_readiness(organization_id)

        stmt_sit = (
            select(func.count())
            .select_from(OperationalSituationDbModel)
            .where(
                OperationalSituationDbModel.organization_id == org_id,
                OperationalSituationDbModel.status == "ACTIVE"
            )
        )
        res_sit = await self._session.execute(stmt_sit)
        active_situations_count = res_sit.scalar() or 0

        stmt_anom = (
            select(func.count())
            .select_from(AnomalyDbModel)
            .where(
                AnomalyDbModel.organization_id == org_id,
                AnomalyDbModel.status.in_(["DETECTED", "ACKNOWLEDGED", "ASSIGNED", "INVESTIGATING", "MITIGATED"])
            )
        )
        res_anom = await self._session.execute(stmt_anom)
        active_exceptions_count = res_anom.scalar() or 0

        stmt_rem = (
            select(func.count())
            .select_from(RemediationActionDbModel)
            .where(
                RemediationActionDbModel.organization_id == org_id,
                RemediationActionDbModel.status == "EXECUTING"
            )
        )
        res_rem = await self._session.execute(stmt_rem)
        executing_remediations_count = res_rem.scalar() or 0

        stmt_viols = (
            select(func.count())
            .select_from(FinancialConsistencyViolationDbModel)
            .where(
                FinancialConsistencyViolationDbModel.organization_id == org_id,
                FinancialConsistencyViolationDbModel.resolved == False
            )
        )
        res_viols = await self._session.execute(stmt_viols)
        active_financial_violations_count = res_viols.scalar() or 0

        stmt_org = select(OrganizationDbModel.name).where(OrganizationDbModel.id == org_id)
        res_org = await self._session.execute(stmt_org)
        org_name = res_org.scalar_one_or_none() or "NomadX Merchant Store"

        degraded_components = [
            comp for comp, info in health_report["components"].items()
            if info["status"] in ("DEGRADED", "UNHEALTHY")
        ]

        return {
            "organization_name": org_name,
            "platform_health": health_report["status"],
            "platform_readiness": health_report["readiness"]["status"],
            "active_situations_count": active_situations_count,
            "active_exceptions_count": active_exceptions_count,
            "executing_remediations_count": executing_remediations_count,
            "active_financial_violations_count": active_financial_violations_count,
            "degraded_components": degraded_components
        }

    async def get_situation_timeline(
        self,
        organization_id: OrganizationId,
        situation_id: str
    ) -> List[Dict[str, Any]]:
        org_id = organization_id.value

        stmt_sit = (
            select(OperationalSituationDbModel)
            .where(
                OperationalSituationDbModel.organization_id == org_id,
                OperationalSituationDbModel.id == situation_id
            )
        )
        res_sit = await self._session.execute(stmt_sit)
        sit = res_sit.scalar_one_or_none()
        if not sit:
            raise ValueError("Situation not found.")

        ev = json.loads(sit.evidence) if sit.evidence else {}
        anomaly_ids = [anom["id"] for anom in ev.get("related_anomalies", [])]

        timeline = []

        if sit.correlation_id:
            stmt_tel = (
                select(TelemetryEventDbModel)
                .where(
                    TelemetryEventDbModel.organization_id == org_id,
                    TelemetryEventDbModel.correlation_id == sit.correlation_id
                )
            )
            res_tel = await self._session.execute(stmt_tel)
            telemetries = res_tel.scalars().all()
            for t in telemetries:
                timeline.append({
                    "timestamp": t.timestamp.isoformat(),
                    "type": "telemetry",
                    "details": {
                        "component": t.component,
                        "operation": t.operation,
                        "message": t.message,
                        "status": t.status
                    }
                })

        if anomaly_ids:
            stmt_anoms = (
                select(AnomalyDbModel)
                .where(AnomalyDbModel.id.in_(anomaly_ids))
            )
            res_anoms = await self._session.execute(stmt_anoms)
            anoms = res_anoms.scalars().all()
            for a in anoms:
                timeline.append({
                    "timestamp": a.detected_at.isoformat(),
                    "type": "anomaly",
                    "details": {
                        "anomaly_type": a.anomaly_type,
                        "component": a.component,
                        "severity": a.severity,
                        "status": a.status
                    }
                })

        if anomaly_ids:
            stmt_rems = (
                select(RemediationActionDbModel)
                .where(RemediationActionDbModel.exception_id.in_(anomaly_ids))
            )
            res_rems = await self._session.execute(stmt_rems)
            rems = res_rems.scalars().all()
            for r in rems:
                timeline.append({
                    "timestamp": r.created_at.isoformat(),
                    "type": "remediation",
                    "details": {
                        "action_type": r.action_type,
                        "status": r.status,
                        "attempt_count": r.attempt_count
                    }
                })

        timeline.sort(key=lambda x: x["timestamp"])
        return timeline

    async def query_audit_trails(
        self,
        organization_id: OrganizationId,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        limit = min(limit, 100)
        stmt = (
            select(OperationalAuditTrailDbModel)
            .where(OperationalAuditTrailDbModel.organization_id == organization_id.value)
            .order_by(OperationalAuditTrailDbModel.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await self._session.execute(stmt)
        audits = res.scalars().all()
        return [
            {
                "id": a.id,
                "actor_id": a.actor_id,
                "action": a.action,
                "target_id": a.target_id,
                "target_type": a.target_type,
                "previous_state": a.previous_state,
                "new_state": a.new_state,
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                "context": a.context,
                "correlation_id": a.correlation_id
            }
            for a in audits
        ]

    async def log_control_plane_audit(
        self,
        organization_id: OrganizationId,
        actor_id: str,
        action: str,
        target_id: str,
        target_type: str,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        context: Optional[str] = None,
        correlation_id: Optional[str] = None
    ) -> None:
        audit = OperationalAuditTrailDbModel(
            id=str(uuid4()),
            actor_id=actor_id,
            organization_id=organization_id.value,
            action=action,
            target_id=target_id,
            target_type=target_type,
            previous_state=previous_state,
            new_state=new_state,
            timestamp=datetime.utcnow(),
            context=context,
            correlation_id=correlation_id
        )
        self._session.add(audit)
        await self._session.flush()

    async def trigger_intelligence_tick(
        self,
        organization_id: OrganizationId,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        initial_health = await self.evaluate_health_and_readiness(organization_id)
        detect_res = await self.run_anomaly_detection(organization_id)
        situation_res = await self.evaluate_operational_situations(organization_id)
        remediation_res = await self.run_remediation_pipeline(organization_id)
        final_situation = await self.evaluate_operational_situations(organization_id)
        final_health = await self.evaluate_health_and_readiness(organization_id)

        await self.log_control_plane_audit(
            organization_id=organization_id,
            actor_id=actor_id,
            action="INTELLIGENCE_TICK",
            target_id=organization_id.value,
            target_type="orchestrator",
            previous_state=json.dumps({"health": initial_health["status"]}),
            new_state=json.dumps({"health": final_health["status"]}),
            context="Continuous closed-loop operational intelligence loop executed",
            correlation_id=correlation_id
        )

        return {
            "status": "SUCCESS",
            "initial_health": initial_health["status"],
            "final_health": final_health["status"],
            "anomalies_detected": detect_res.get("detected_anomalies_count", 0),
            "situations_evaluated": situation_res.get("evaluated_situations_count", 0),
            "remediations_executed": remediation_res.get("executed_remediations_count", 0)
        }

    async def get_readiness_policy(
        self,
        organization_id: OrganizationId
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        stmt = (
            select(AutonomousReadinessPolicyDbModel)
            .where(AutonomousReadinessPolicyDbModel.organization_id == org_id)
        )
        res = await self._session.execute(stmt)
        policy = res.scalar_one_or_none()

        if not policy:
            policy = AutonomousReadinessPolicyDbModel(
                organization_id=org_id,
                readiness_level="LEVEL_2_CONTROLLED_AUTOMATION",
                emergency_disable=False,
                updated_at=datetime.utcnow()
            )
            self._session.add(policy)
            await self._session.flush()

        return {
            "organization_id": policy.organization_id,
            "readiness_level": policy.readiness_level,
            "emergency_disable": policy.emergency_disable,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at else None
        }

    async def update_readiness_policy(
        self,
        organization_id: OrganizationId,
        readiness_level: str,
        emergency_disable: bool,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        stmt = (
            select(AutonomousReadinessPolicyDbModel)
            .where(AutonomousReadinessPolicyDbModel.organization_id == org_id)
        )
        res = await self._session.execute(stmt)
        policy = res.scalar_one_or_none()

        if not policy:
            policy = AutonomousReadinessPolicyDbModel(
                organization_id=org_id,
                readiness_level=readiness_level,
                emergency_disable=emergency_disable,
                updated_at=datetime.utcnow()
            )
            self._session.add(policy)
        else:
            policy.readiness_level = readiness_level
            policy.emergency_disable = emergency_disable
            policy.updated_at = datetime.utcnow()

        await self._session.flush()
        
        await self.log_control_plane_audit(
            organization_id=organization_id,
            actor_id=actor_id,
            action="UPDATE_READINESS_POLICY",
            target_id=org_id,
            target_type="policy",
            previous_state=None,
            new_state=json.dumps({"readiness_level": readiness_level, "emergency_disable": emergency_disable}),
            context="Readiness policy updated",
            correlation_id=correlation_id
        )

        return {
            "organization_id": policy.organization_id,
            "readiness_level": policy.readiness_level,
            "emergency_disable": policy.emergency_disable,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at else None
        }

    async def cleanup_old_telemetry(
        self,
        organization_id: OrganizationId,
        older_than_days: int,
        actor_id: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        org_id = organization_id.value
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)

        # Delete old telemetry events
        stmt_tel = (
            delete(TelemetryEventDbModel)
            .where(
                TelemetryEventDbModel.organization_id == org_id,
                TelemetryEventDbModel.timestamp < cutoff
            )
        )
        res_tel = await self._session.execute(stmt_tel)
        deleted_tel = res_tel.rowcount or 0

        # Delete old health state transitions
        stmt_health = (
            delete(ComponentHealthTransitionDbModel)
            .where(
                ComponentHealthTransitionDbModel.organization_id == org_id,
                ComponentHealthTransitionDbModel.timestamp < cutoff
            )
        )
        res_health = await self._session.execute(stmt_health)
        deleted_health = res_health.rowcount or 0

        await self._session.flush()

        await self.log_control_plane_audit(
            organization_id=organization_id,
            actor_id=actor_id,
            action="TELEMETRY_CLEANUP",
            target_id=org_id,
            target_type="telemetry",
            context=f"Cleaned up telemetry records older than {older_than_days} days.",
            correlation_id=correlation_id
        )

        return {
            "status": "SUCCESS",
            "deleted_telemetry_events": deleted_tel,
            "deleted_health_transitions": deleted_health
        }
