import os
import sys
import subprocess
import asyncio
from datetime import datetime

def run_step(step_name: str, cmd: list[str]) -> bool:
    print(f"\n[{step_name}] Executing command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  -> {step_name}: PASS")
        return True
    else:
        print(f"  -> {step_name}: FAIL")
        print(f"Error output:\n{result.stderr or result.stdout}")
        return False

async def main():
    print("=" * 65)
    print("NomadX Zero-Downtime Production Deployment Orchestrator")
    print(f"Timestamp: {datetime.utcnow().isoformat()} UTC")
    print("=" * 65)

    # Step 1: Pre-flight secrets entropy check
    if not run_step("Step 1: Security Posture Diagnostics", [sys.executable, "scripts/activate_production.py"]):
        print("\n[DEPLOYMENT ABORTED]: Security posture check failed.")
        sys.exit(1)

    # Step 2: Database Schema Upgrade (Alembic)
    if not run_step("Step 2: Database Schema Migrations (Alembic)", [sys.executable, "-m", "alembic", "upgrade", "head"]):
        print("\n[DEPLOYMENT ABORTED]: Database migration failed.")
        sys.exit(1)

    # Step 3: End-to-End Canary Validation Gate
    if not run_step("Step 3: End-to-End Canary Validation Gate", [sys.executable, "scripts/canary_validation.py"]):
        print("\n[DEPLOYMENT ABORTED]: Canary validation failed.")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("STATUS: ZERO-DOWNTIME PRODUCTION DEPLOYMENT COMPLETE (PASSED ALL GATES)")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
