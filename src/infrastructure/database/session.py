from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class DatabaseSessionManager:
    """
    Manages SQLAlchemy asynchronous engine and session maker lifecycle.
    """
    def __init__(self):
        self._engine = None
        self._session_maker = None

    def init(self, db_url: str) -> None:
        """Initializes engine and session maker with the specified connection string."""
        if self._engine is not None:
            return  # Already initialized

        # For sqlite, we often need to configure pool parameters or disable thread checks
        connect_args = {}
        extra_args = {}
        if db_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in db_url:
                from sqlalchemy.pool import StaticPool
                extra_args = {"poolclass": StaticPool}
            else:
                from sqlalchemy.pool import NullPool
                extra_args = {"poolclass": NullPool}
        else:
            from nomadx.src.infrastructure.config.configuration import settings
            # PostgreSQL optimized pooling & SSL parameters
            if settings.db_ssl_mode and settings.env == "production":
                connect_args["ssl"] = settings.db_ssl_mode
            extra_args = {
                "pool_size": settings.db_pool_size,
                "max_overflow": settings.db_max_overflow,
                "pool_pre_ping": settings.db_pool_pre_ping
            }

        self._engine = create_async_engine(
            db_url,
            echo=False,
            connect_args=connect_args,
            **extra_args
        )
        
        if db_url.startswith("sqlite"):
            from sqlalchemy import event
            @event.listens_for(self._engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        self._session_maker = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False
        )

    async def close(self) -> None:
        """Closes the underlying database engine connection pool."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_maker = None

    async def create_tables(self) -> None:
        """Ensures all ORM model tables are created in the underlying database."""
        if not self._engine:
            raise RuntimeError("DatabaseSessionManager is not initialized. Call init() first.")
        import nomadx.src.infrastructure.database.models  # noqa: F401 Ensure models registered
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


    @property
    def engine(self):
        if not self._engine:
            raise RuntimeError("DatabaseSessionManager is not initialized. Call init() first.")
        return self._engine

    @property
    def session_maker(self):
        if not self._session_maker:
            raise RuntimeError("DatabaseSessionManager is not initialized. Call init() first.")
        return self._session_maker

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provides a context-managed transactional AsyncSession."""
        if not self._session_maker:
            raise RuntimeError("DatabaseSessionManager is not initialized. Call init() first.")
        
        session = self._session_maker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

# Export a global singleton session manager
session_manager = DatabaseSessionManager()
