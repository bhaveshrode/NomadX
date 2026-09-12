import sys
import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest_asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import event, Engine, text

from nomadx.src.infrastructure.database import Base, session_manager

# Use an in-memory SQLite database by default, or point to a custom URL (e.g. PostgreSQL) via env var
TEST_DB_URL = os.getenv("TEST_DB_URL", "sqlite+aiosqlite:///:memory:")

# Register event listener to strip timezone from datetime parameters for PostgreSQL tests
def _strip_tz(val):
    if isinstance(val, datetime) and val.tzinfo is not None:
        return val.astimezone(timezone.utc).replace(tzinfo=None)
    return val

@event.listens_for(Engine, "before_cursor_execute", retval=True)
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if parameters:
        if isinstance(parameters, dict):
            parameters = {k: _strip_tz(v) for k, v in parameters.items()}
        elif isinstance(parameters, list):
            new_params = []
            for p in parameters:
                if isinstance(p, dict):
                    new_params.append({k: _strip_tz(v) for k, v in p.items()})
                elif isinstance(p, (tuple, list)):
                    new_params.append(type(p)(_strip_tz(v) for v in p))
                else:
                    new_params.append(_strip_tz(p))
            parameters = new_params
        elif isinstance(parameters, tuple):
            parameters = tuple(_strip_tz(v) for v in parameters)
    return statement, parameters


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    """
    Initializes test database schemas before each test function.
    """
    # Override settings to use the test DB URL
    from nomadx.src.bootstrap.container import container
    container.settings.db_url = TEST_DB_URL
    
    session_manager.init(TEST_DB_URL)
    
    if "postgresql" in TEST_DB_URL:
        # PostgreSQL: Assume tables are already created via migrations
        # 1. Ensure triggers are disabled to bypass foreign key constraints in tests
        # We run each in a fresh connection checkout to isolate transaction failures
        for table_name in Base.metadata.tables.keys():
            try:
                async with session_manager.engine.begin() as conn:
                    await conn.execute(text(f'ALTER TABLE "{table_name}" DISABLE TRIGGER ALL'))
            except Exception:
                pass
        
        # 2. Truncate all tables to ensure a clean state
        table_names = ", ".join(f'"{table}"' for table in Base.metadata.tables.keys())
        async with session_manager.engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
            
        yield
        # Keep engine open and do not drop tables for next test
    else:
        # SQLite behavior: create and drop for every test
        async with session_manager.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        yield
        
        async with session_manager.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            
        await session_manager.close()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an active database session for raw database interactions.
    """
    async with session_manager.session() as session:
        yield session
