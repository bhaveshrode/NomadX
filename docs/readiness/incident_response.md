# NomadX Production Incident Response Playbook

This document defines the protocols, triage steps, diagnostic queries, and remediation procedures for resolving high-priority incidents in the NomadX production environment.

---

## Incident 1: Database Outages & Connection Saturation

### Symptoms & Logs
* Web API endpoints return `500 Internal Server Error` or timeout.
* Logs show: `sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached, connection timed out`.

### Diagnostic Commands
Identify active database connections and long-running locking queries:
```sql
-- View total active connections
SELECT count(*), state FROM pg_stat_activity GROUP BY state;

-- Identify queries active for more than 5 minutes
SELECT pid, age(query_start, clock_timestamp()), usename, query, state
FROM pg_stat_activity
WHERE state != 'idle' AND query_start < clock_timestamp() - INTERVAL '5 minutes'
ORDER BY query_start ASC;
```

### Remediation Actions
1. **Kill Blocking Queries**:
   If a specific query is blocking connection pools, terminate it:
   ```sql
   SELECT pg_terminate_backend(blocking_pid);
   ```
2. **Increase Connection Pool Limits**:
   If genuine load is saturating connections, adjust pool settings in `.env`:
   ```bash
   DB_POOL_SIZE=40
   DB_MAX_OVERFLOW=20
   ```
   Then restart services:
   ```bash
   docker compose up -d nomadx-api nomadx-worker
   ```

---

## Incident 2: Background Worker Failures

### Symptoms & Logs
* Transaction events remain stuck in `PENDING` or `PROCESSING` state indefinitely.
* Outbox event queue does not drain.
* Logs show: `Background loop 'ar_event_processor' has stopped` or `Unhandled exception in background loop`.

### Diagnostic Commands
Check worker logs to determine which loop crashed:
```bash
docker compose logs nomadx-worker | grep -E "ERROR|CRITICAL"
```
Verify loop task status via our observability dashboard:
```bash
curl -k -H "Authorization: Bearer <ADMIN_TOKEN>" \
     https://localhost/api/observability/components
```

### Remediation Actions
The background scheduler is designed to isolate loop failures (see [main.py (Worker)](file:///d:/Blockchain_Projects/NomadX/apps/worker/main.py#L93-L94)), but a full process freeze requires container restart:
1. Restart the background worker container:
   ```bash
   docker compose restart nomadx-worker
   ```
2. Trigger the recovery of stuck outbox and ledger events manually:
   ```bash
   curl -i -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
        https://localhost/api/financial-recovery/recover-stuck
   ```

---

## Incident 3: Financial Consistency & Double-Allocation Violations

### Symptoms & Logs
* Observability alert or webhook triggers.
* Health status reports `"readiness": {"status": "NOT_READY"}` with barrier `"Active financial consistency violations present"`.
* Logs show: `CRITICAL [FinancialConsistency] Balance mismatch detected for organization...`

### Diagnostic Commands
Retrieve the detailed consistency report to identify the violating aggregate context (e.g. receivables, invoices, allocation balances):
```bash
curl -k -H "Authorization: Bearer <ADMIN_TOKEN>" \
     https://localhost/api/financial-consistency/status
```

### Remediation Actions
1. **Identify Correlation**: Locate the conflicting entities using trace endpoints:
   ```bash
   curl -k -H "Authorization: Bearer <ADMIN_TOKEN>" \
        https://localhost/api/observability/traces/<correlation_id>
   ```
2. **Execute Remediation Pipeline**:
   Run the closed-loop remediation pipeline to scan and fix consistency balances:
   ```bash
   curl -i -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
        https://localhost/api/observability/remediations/run
   ```
3. **Manual Override**:
   If autonomous remediation safety rules block automatic resolution, invoke manual recovery using target event IDs:
   ```bash
   curl -i -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
        https://localhost/api/financial-recovery/retry-event/<outbox_event_id>
   ```

---

## Incident 4: Security Compromise & Token Leaks

### Symptoms & Logs
* Logs display access attempts to restricted tenant domains using invalid signatures.
* Trace audits report unusual high volume transactions from unrecognized client IP addresses.

### Diagnostic Commands
Retrieve the administrative audit trail to view security event details and identify compromised users/tokens:
```bash
curl -k -H "Authorization: Bearer <PLATFORM_ADMIN_TOKEN>" \
     https://localhost/api/observability/control-plane/audit-trail
```

### Remediation Actions
1. **Revoke compromised session token**:
   Rotate the application's core keys. This instantly invalidates all issued JWT tokens, requiring all active users to re-login:
   * Generate a new cryptographically secure secret:
     ```bash
     openssl rand -hex 32
     ```
   * Update `JWT_SECRET` in `.env`.
   * Restart Web API and Gateway:
     ```bash
     docker compose up -d nomadx-api nginx-gateway
     ```
2. **Lock Compromised User Account**:
   To disable a specific user credential temporarily:
   ```sql
   UPDATE users SET status = 'LOCKED' WHERE email = 'compromised_user@example.com';
   ```

---

## Incident 5: Autonomous-Remediation Loop Failure

### Symptoms & Logs
* The system detects an operational exception but triggers remediation actions in an infinite failure loop.
* Logs show: `RemediationAction failed. Retry attempt 10. Limit reached.`

### Diagnostic Commands
Query failed remediation actions:
```bash
curl -k -H "Authorization: Bearer <ADMIN_TOKEN>" \
     https://localhost/api/observability/remediations?status=FAILED
```

### Remediation Actions
1. **Temporarily Disable Autonomous Actions**:
   Change the organization readiness policy level to `LEVEL_0_OBSERVATIONAL` and enable the emergency shutdown flag. This prevents worker processes from running automated mitigation tasks:
   ```bash
   curl -i -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
        -H "Content-Type: application/json" \
        -d '{"readiness_level": "LEVEL_0_OBSERVATIONAL", "emergency_disable": true}' \
        https://localhost/api/observability/intelligence/policy
   ```
2. **Investigate the Failure**:
   Inspect the specific remediation details:
   ```bash
   curl -k -H "Authorization: Bearer <ADMIN_TOKEN>" \
        https://localhost/api/observability/remediations/<remediation_id>
   ```
3. **Resolve and Resume**:
   Once the underlying resource (e.g. database locks, RPC node availability) is resolved, restore the autonomous policy:
   ```bash
   curl -i -X POST -H "Authorization: Bearer <ADMIN_TOKEN>" \
        -H "Content-Type: application/json" \
        -d '{"readiness_level": "LEVEL_2_AUTONOMOUS", "emergency_disable": false}' \
        https://localhost/api/observability/intelligence/policy
   ```
