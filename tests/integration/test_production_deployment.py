import pytest
import os
import sys
import subprocess

@pytest.mark.asyncio
async def test_activate_production_script():
    """Verify activate_production.py executes cleanly and returns exit code 0."""
    result = subprocess.run([sys.executable, "scripts/activate_production.py"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "PRODUCTION ENVIRONMENT READY" in result.stdout

@pytest.mark.asyncio
async def test_canary_validation_script():
    """Verify canary_validation.py executes cleanly and returns exit code 0."""
    result = subprocess.run([sys.executable, "scripts/canary_validation.py"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "CANARY VALIDATION RESULT: ALL STAGES PASSED CLEANLY" in result.stdout
