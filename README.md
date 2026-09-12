# NomadX

NomadX is an enterprise-grade autonomous commerce application built in Python.

## Architectural Principles
NomadX is designed using clean architecture and Domain-Driven Design (DDD) principles:
- **Language Independence**: Built from the ground up in Python to prove that the architecture defined by ACOS (Autonomous Commerce OS) is language-agnostic.
- **Strict Dependency Boundaries**:
  - `domain/`: Dependency-free core business logic (Value Objects, Entities, Aggregates, Specifications, Domain Events).
  - `application/`: Orchestration of use cases (Commands, Queries, Handlers).
  - `infrastructure/`: Database clients, ORM models, message queues, external APIs, and adapters.
  - `presentation/`: API endpoints, controllers, and transport protocols.
  - `bootstrap/`: Composition root for wiring components.

## Tech Stack
- **Python**: 3.11+
- **Framework**: FastAPI
- **Validation**: Pydantic v2
- **Database**: PostgreSQL with SQLAlchemy 2.0 (asyncpg) and Alembic
- **Testing**: pytest & pytest-asyncio

## Setup & Testing
1. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
2. Run tests:
   ```bash
   pytest
   ```
