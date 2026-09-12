# NomadX Production Deployment Runbook

This document details the step-by-step procedure to deploy the NomadX system into a production environment.

## 1. Prerequisites & System Limits

Before starting the deployment, verify the target system meets these requirements:
* **Docker Engine**: v24.0+
* **Docker Compose**: v2.20+
* **System Resource Limits**:
  * Set max file descriptors to at least `65535` (essential for high-concurrency API and database connection pooling):
    ```bash
    ulimit -n 65535
    ```
* **Network Ports Ingress**:
  * Port `80`: Ingress for HTTP (redirects to HTTPS)
  * Port `443`: Ingress for secure HTTPS API Gateway
  * Port `5432` (Optional): Internal/ingress restricted to DB backups/monitoring

---

## 2. Secrets & Configuration Setup

NomadX uses Pydantic Settings loaded from environment variables or a `.env` file at the app root. 
In production, strict security validations are performed in [configuration.py](file:///d:/Blockchain_Projects/NomadX/src/infrastructure/config/configuration.py). The application **will refuse to start** if development default keys are used.

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Populate `.env` with secure production values:

| Variable | Description | Production Requirement |
| :--- | :--- | :--- |
| `ENV` | Application environment | Must be set to `production` |
| `DB_URL` | PostgreSQL Connection URI | `postgresql+asyncpg://<user>:<password>@<host>:<port>/<db>` |
| `JWT_SECRET` | Token signing secret key | Must be overridden, minimum 32 characters, cryptographically secure |
| `INTERNAL_SECRET`| Internal service authentication | Must be overridden, minimum 16 characters |
| `BASE_RPC_URL` | Base network RPC Endpoint | Point to a secure private node provider (e.g. Alchemy, Infura) |

Generate secure secrets on the deployment host:
```bash
# Generate secure 32-character JWT_SECRET
openssl rand -hex 32

# Generate secure 16-character INTERNAL_SECRET
openssl rand -hex 16
```

---

## 3. Database Initialization & Connection Limits

Verify that the target PostgreSQL cluster has appropriate parameters set to handle NomadX pooling settings.
NomadX's connection pool config uses:
* `db_pool_size = 20` (per process)
* `db_max_overflow = 10` (per process)

Ensure `max_connections` in your `postgresql.conf` is configured to accommodate connection pool sizing:
$$\text{max\_connections} \ge ((\text{API Replicas} + \text{Worker Replicas}) \times (\text{db\_pool\_size} + \text{db\_max\_overflow})) + 10$$

To check database availability:
```bash
docker compose exec postgres pg_isready -U postgres
```

---

## 4. Database Migrations

NomadX utilizes Alembic to handle schema migrations. The migration schema matches our models and is defined in [migrations/versions/754be8788321_initial_schema.py](file:///d:/Blockchain_Projects/NomadX/migrations/versions/754be8788321_initial_schema.py).

Migrations are executed automatically on API container startup via [entrypoint.sh](file:///d:/Blockchain_Projects/NomadX/scripts/entrypoint.sh#L8-L9). However, to run migrations manually or verify schema version compliance:

```bash
# Apply migrations manually
docker compose run --rm nomadx-api alembic upgrade head

# Check current database migration version status
docker compose run --rm nomadx-api alembic current
```

---

## 5. Deployment Commands

NomadX services are fully containerized using [docker-compose.yml](file:///d:/Blockchain_Projects/NomadX/docker-compose.yml).

### Step 5.1: Build images
```bash
docker compose build
```

### Step 5.2: Launch Infrastructure & Application Services
Deploy the containers in the correct order using the environment variables defined in `.env`:
```bash
# Start Database and Redis Cache first
docker compose up -d postgres redis

# Verify backing service health
docker compose ps

# Start Web API and background processing Workers
docker compose up -d nomadx-api nomadx-worker

# Launch API Nginx Gateway
docker compose up -d nginx-gateway
```

---

## 6. API Gateway & TLS Integration

The Nginx Reverse Proxy acts as the gateway to the NomadX Web API.
1. Place production SSL/TLS certificates on the host machine:
   * Certificate: `/etc/ssl/certs/nomadx.crt`
   * Private Key: `/etc/ssl/private/nomadx.key`
2. Update volumes in your [docker-compose.yml](file:///d:/Blockchain_Projects/NomadX/docker-compose.yml#L84-L85) if mounting certs from custom host directories:
   ```yaml
   nginx-gateway:
     volumes:
       - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
       - /etc/ssl/certs/nomadx.crt:/etc/ssl/certs/nginx-selfsigned.crt:ro
       - /etc/ssl/private/nomadx.key:/etc/ssl/private/nginx-selfsigned.key:ro
   ```
3. Reload Nginx to apply changes:
   ```bash
   docker compose exec nginx-gateway nginx -s reload
   ```

---

## 7. Health & Verification Probes

After starting the services, execute health probes to verify deployment success:

### Liveness and Database Connectivity Verification
Request the health endpoint via the gateway:
```bash
curl -k -i https://localhost/health
```
**Expected Response Status**: `200 OK`
**Expected Response Body**:
```json
{
  "status": "healthy",
  "service": "nomadx-api",
  "timestamp": "2026-08-28T07:00:00.000000",
  "dependencies": {
    "database": "healthy"
  }
}
```

### Background Workers Health Check
Verify worker status by checking process container logs:
```bash
docker compose logs --tail=100 nomadx-worker
```
**Expected Log output**:
```text
[NomadXWorker]: NomadX Background Scheduler running. Coordinating 16 background loops.
[NomadXWorker]: Loop 'outbox_publisher' started (polling every 2.0s).
[NomadXWorker]: Loop 'ar_event_processor' started (polling every 2.0s).
...
```
