from typing import Optional
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.models import BlockchainCursorDbModel


class BlockchainCursorRepository:
    """
    Manages scanning block height cursors stored inside PostgreSQL.
    """
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_checkpoint(self, network: str) -> Optional[int]:
        """Retrieves last processed block height for the network."""
        result = await self._session.execute(
            select(BlockchainCursorDbModel).where(BlockchainCursorDbModel.network == network.upper())
        )
        model = result.scalar_one_or_none()
        return model.last_processed_block if model else None

    async def update_checkpoint(self, network: str, block_number: int) -> None:
        """Saves new checkpoint height for the network."""
        model = BlockchainCursorDbModel(
            network=network.upper(),
            last_processed_block=block_number,
            updated_at=datetime.utcnow()
        )
        await self._session.merge(model)
        await self._session.flush()
