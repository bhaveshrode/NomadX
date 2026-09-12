import pytest
from nomadx.scripts.canary_validation import run_canary_validation

@pytest.mark.asyncio
async def test_production_canary_validation_flow():
    await run_canary_validation()
