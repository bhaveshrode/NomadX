# NomadX - Autonomous Commerce System

![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Framework](https://img.shields.io/badge/framework-FastAPI-009688.svg)
![Database](https://img.shields.io/badge/database-PostgreSQL%20%2B%20SQLAlchemy%202.0-blue)
![Architecture](https://img.shields.io/badge/architecture-DDD%20%2F%20Clean%20Architecture-orange)
![License](https://img.shields.io/badge/license-MIT-green.svg)

NomadX is an enterprise-grade, domain-driven autonomous commerce application built in Python. Designed as a language-agnostic realization of the architecture defined by **Autonomous Commerce OS (ACOS)**, NomadX provides robust merchant lifecycle management, invoice processing, double-entry accounts receivable, automated payment reconciliation, and multi-tenant security operations.

---

## 🏛️ Architectural Principles & Design

NomadX follows strict **Domain-Driven Design (DDD)** and **Clean Architecture** principles to maintain strict separation of concerns, language independence, and testability:

- **Language Independence**: Built from the ground up in Python to prove that the kernel architecture defined by ACOS is language-agnostic.
- **Strict Dependency Boundaries**:
  - `src/domain/`: Dependency-free core business rules (Value Objects, Entities, Aggregates, Specifications, Domain Events).
  - `src/application/`: Application use cases, commands, queries, event handlers, and workflow orchestration.
  - `src/infrastructure/`: Async ORM models (SQLAlchemy 2.0 / `asyncpg`), database clients, Alembic migrations, Redis queues, and blockchain adapters.
  - `src/presentation/`: FastAPI REST API endpoints, routing, request/response models, and static console dashboards.
  - `src/bootstrap/`: Composition root responsible for dependency injection and application initialization.
- **Decoupled Executables**:
  - `apps/api/`: Main FastAPI REST service entrypoint.
  - `apps/worker/`: Asynchronous event and reconciliation worker service entrypoint.

---

## ⚡ Core Subsystems & Capabilities

1. **Identity & Tenant Management**: Multi-tenant merchant onboarding, organization parameters, role-based access control, cryptographic JWT issuance, and password hashing (`passlib` + `bcrypt`).
2. **Invoice & Accounts Receivable (AR)**: Complete invoice document lifecycles (`DRAFT ➔ ISSUED ➔ SENT ➔ PAID / OVERPAID`), AR aging reports, dunning automation, and overpayment balances.
3. **Payment Processing & Intents**: Gateway integrations, payment intent collection tracking, settlement verification, and asynchronous event notifications.
4. **Settlement & Reconciliation Engine**: Automated matching of incoming payment gateway webhooks against outstanding customer invoices and ledger entries, with blockchain verification adapters.
5. **Workflow Orchestration & Approvals**: Execution state machines for multi-step approvals, automated remediation, financial recovery, and idempotency guarantees.
6. **Observability & Security Operations**: SLO metric reporting, anomaly detection, system audit logs, and legal/regulatory compliance tracking.

---

## 📁 Repository Structure

```
NomadX/
├── .github/                # CI/CD workflows and GitHub Actions
├── alembic.ini             # Alembic database migration configuration
├── apps/                   # Application entrypoints
│   ├── api/                # FastAPI HTTP web server launcher
│   └── worker/             # Background event worker launcher
├── docs/                   # System documentation & readiness checklists
├── Dockerfile              # Multi-stage production container build definition
├── docker-compose.yml      # Orchestration for API, Worker, PostgreSQL, Redis & Nginx
├── migrations/             # Alembic database migration scripts & versions
├── nginx/                  # Nginx reverse proxy gateway configuration
├── pyproject.toml          # Build configuration and dependency definitions
├── README.md               # Project documentation
├── scripts/                # Operational deployment, backup, and canary scripts
├── src/                    # Core DDD source code
│   ├── application/        # Application services & use cases
│   ├── bootstrap/          # Composition root & DI container
│   ├── domain/             # Domain logic (Entities, Value Objects, Aggregates)
│   ├── infrastructure/     # Persistence, ORM, Redis, Blockchain, & Workers
│   └── presentation/       # FastAPI REST endpoints & static web console
└── tests/                  # Automated test suites
    ├── concurrency/        # Concurrency & lock testing
    ├── integration/        # E2E & API integration tests
    ├── performance/        # Load & database integrity tests
    ├── pilots/             # Partner simulation tests
    ├── production/         # Canary validation tests
    ├── resilience/         # Failure injection & recovery tests
    ├── security/           # Security hardening & isolation tests
    └── unit/               # Domain & logic unit tests
```

---

## 🛠️ Tech Stack & Prerequisites

### Prerequisites
- **Python**: v3.11 or higher
- **PostgreSQL**: v14.0 or higher (or SQLite for local lightweight testing)
- **Redis**: v7.0 or higher (for caching and task queues)

### Core Dependencies
- **FastAPI** (`>=0.110.0`) & **Uvicorn**
- **Pydantic v2** (`>=2.6.0`)
- **SQLAlchemy 2.0** (`>=2.0.28`) with **asyncpg** & **Alembic**
- **PyJWT** & **Passlib (bcrypt)**

---

## 🚀 Getting Started

### 1. Installation

Clone the repository and install the project in editable mode with development dependencies:

```bash
git clone https://github.com/bhaveshrode/NomadX.git
cd NomadX

# Create and activate virtual environment
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"
```

### 2. Environment Configuration

Copy the example environment configuration file:

```bash
cp .env.example .env
```

Adjust `.env` parameters as needed for database credentials, JWT secret keys, and service settings.

### 3. Database Migrations

Run database migrations using Alembic:

```bash
alembic upgrade head
```

### 4. Running Locally

#### Run the REST API Server:
```bash
python -m apps.api
# or via Uvicorn directly:
uvicorn src.presentation.api.routes.status:app --reload --port 8000
```
Access the interactive OpenAPI documentation at: `http://localhost:8000/docs`

#### Run the Async Background Worker:
```bash
python -m apps.worker
```

---

## 🐳 Containerized Deployment (Docker Compose)

Launch the full production-like environment (PostgreSQL, Redis, NomadX API, NomadX Worker, and Nginx Gateway) with Docker Compose:

```bash
docker-compose up --build -d
```

Check container health:
```bash
docker-compose ps
```

---

## 🧪 Testing Suite

NomadX includes exhaustive automated test suites categorized by scope:

Run the entire test suite:
```bash
pytest
```

Run specific test categories:
```bash
# Unit Tests
pytest tests/unit/

# Integration & API Tests
pytest tests/integration/

# Resilience & Recovery Tests
pytest tests/resilience/

# Security & Tenant Isolation Tests
pytest tests/security/

# Concurrency & Load Tests
pytest tests/concurrency/
pytest tests/performance/
```

---

## 🛠️ Operational Scripts

NomadX includes utility scripts located in the `scripts/` directory:

- **Secret Generation**: `python scripts/generate_secrets.py`
- **Canary Validation**: `python scripts/canary_validation.py`
- **Production Activation**: `python scripts/activate_production.py`
- **Database Backup & Restore**: `scripts/backup.sh` / `scripts/restore.sh`

---

## 📄 License

This project is licensed under the [MIT License](pyproject.toml).
