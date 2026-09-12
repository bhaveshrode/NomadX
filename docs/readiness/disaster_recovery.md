# NomadX Disaster Recovery & Business Continuity Plan

This document establishes the targets, procedures, and checklists for restoring NomadX platform operations following a major disaster, database corruption, or complete loss of hosting infrastructure.

---

## 1. Disaster Recovery Targets (RPO & RTO)

| Service Metric | Target Objective | Strategy to Achieve Target |
| :--- | :--- | :--- |
| **Recovery Point Objective (RPO)** | **< 1 Hour** | Hourly automated PostgreSQL snapshots with transaction logs shipping to an offsite secure storage bucket. |
| **Recovery Time Objective (RTO)** | **< 4 Hours** | Automated infrastructure container rebuild via Docker Compose and scripted database restore. |

---

## 2. Backup & Restore Procedures

NomadX includes pre-configured bash shell scripts for executing backups and restores.

### Step 2.1: Automated Backups
The backup script [backup.sh](file:///d:/Blockchain_Projects/NomadX/scripts/backup.sh) generates compressed PostgreSQL schema and database record dumps, saving them to a backup directory.
* **Cron Setup (Hourly Backup)**:
  Configure a cron task on the host machine to automate hourly backups:
  ```bash
  0 * * * * /bin/bash /app/scripts/backup.sh >> /var/log/nomadx_backup.log 2>&1
  ```

### Step 2.2: Database Restore Execution
In the event of database corruption or hardware rebuilds, execute the [restore.sh](file:///d:/Blockchain_Projects/NomadX/scripts/restore.sh) script:
```bash
# 1. Place the backup file on the host (e.g. nomadx_backup_20260828_120000.sql.gz)
# 2. Run the restore script, pointing to the backup target
FORCE=1 bash /app/scripts/restore.sh /app/backups/nomadx_backup_20260828_120000.sql.gz
```
*Note: Setting `FORCE=1` bypasses the interactive confirmation prompt.*

---

## 3. Disaster Failure Scenarios & Mitigation

### Scenario A: PostgreSQL Storage Corruption or Crash
* **Symptoms**: Uncaught HTTP 500 errors across API endpoints; health checks report `"database": "unhealthy: Connection refused"`.
* **Remediation**:
  1. Stop API Gateway to prevent client traffic:
     ```bash
     docker compose stop nginx-gateway
     ```
  2. Inspect database health logs:
     ```bash
     docker compose logs postgres
     ```
  3. If corruption is isolated, drop the current db and recreate from the last hourly backup:
     ```bash
     docker compose stop nomadx-api nomadx-worker
     FORCE=1 bash /app/scripts/restore.sh /app/backups/latest.sql.gz
     docker compose start nomadx-api nomadx-worker nginx-gateway
     ```

### Scenario B: Complete Host Server Failure (Hardware Loss)
* **Symptoms**: Server is completely unreachable; all services offline.
* **Remediation**:
  1. Spin up a new host server machine matching the prerequisites.
  2. Clone the code repository:
     ```bash
     git clone https://github.com/your-org/NomadX.git /app
     ```
  3. Pull down the latest secure backup archive from your offsite backup bucket.
  4. Restore the `.env` configuration file containing target database and secrets variables.
  5. Launch the containers:
     ```bash
     docker compose up -d postgres redis
     docker compose run --rm nomadx-api alembic upgrade head
     docker compose up -d nomadx-api nomadx-worker nginx-gateway
     ```
  6. Restore the database dump:
     ```bash
     FORCE=1 bash /app/scripts/restore.sh /app/backups/latest.sql.gz
     ```

### Scenario C: Base Network RPC Failure
* **Symptoms**: Settlement workers and payment verification processes throw connection errors or time out when querying Base RPC endpoints.
* **Remediation**:
  1. Update `BASE_RPC_URL` in `.env` to point to a backup RPC node (e.g., swapping from Alchemy to QuickNode).
  2. Restart affected containers to pick up the new endpoint configuration:
     ```bash
     docker compose up -d nomadx-api nomadx-worker
     ```

---

## 4. Recovery Verification Checklist

Once the database and services are restored, execute verification queries to confirm data consistency and financial integrity.

### Step 4.1: API Liveness Check
```bash
curl -k https://localhost/health
```
Verify status is `"healthy"`.

### Step 4.2: Ledger Integrity Check
Verify that total payments allocated do not exceed the receivable amount. Run this SQL query directly in PostgreSQL:
```sql
SELECT r.id, r.outstanding_amount, r.total_amount, SUM(COALESCE(pa.amount, 0)) AS allocated_sum
FROM receivables r
LEFT JOIN payment_allocations pa ON pa.receivable_id = r.id
GROUP BY r.id
HAVING r.outstanding_amount + SUM(COALESCE(pa.amount, 0)) != r.total_amount;
```
**Expected Outcome**: 0 records returned. If records are returned, it indicates a database inconsistency that requires remediation via our financial recovery services.

### Step 4.3: Financial Recovery Service Re-sweep
Trigger a system-wide closed-loop financial consistency check via the API gateway using administrative keys:
```bash
curl -i -X POST -H "Authorization: Bearer <PLATFORM_ADMIN_TOKEN>" \
     https://localhost/api/financial-consistency/automation/run
```
This forces the system's background workers to run integrity scans and repair ledger balances automatically.
