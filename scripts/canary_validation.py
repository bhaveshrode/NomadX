import asyncio
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

from nomadx.src.bootstrap.container import container
from nomadx.src.infrastructure.database.session import session_manager

async def run_canary_validation():
    print("=" * 60)
    print("NomadX Production Smoke & Canary Validation")
    print("=" * 60)
    
    container.init_resources()
    await session_manager.create_tables()
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # Step 1: User Registration
        print("[Step 1] Registering Canary User...")
        reg_res = await ac.post("/auth/register", json={
            "email": "canary_user@nomadx.io",
            "password": "CanaryPassword123!",
            "name": "Canary Operator",
            "organization_name": "Canary Test Corp",
            "organization_slug": "canary-test-corp",
            "default_currency": "USD"
        })
        assert reg_res.status_code in [201, 409], f"Registration failed: {reg_res.text}"
        print("  -> Auth & User Registration: PASS")

        # Step 2: Authentication Login
        print("[Step 2] Authenticating Login...")
        login_res = await ac.post("/auth/login", json={
            "email": "canary_user@nomadx.io",
            "password": "CanaryPassword123!"
        })
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("  -> JWT Authentication: PASS")

        # Step 3: Organization Context Verification
        print("[Step 3] Verifying Organization Context...")
        ctx_res = await ac.get("/api/secure-context", headers=headers)
        assert ctx_res.status_code == 200, f"Secure context failed: {ctx_res.text}"
        print("  -> Organization Boundary Isolation: PASS")

        # Step 4: Business Workflow & Invoice Creation
        print("[Step 4] Executing Business Workflow (Invoice Generation)...")
        inv_res = await ac.post("/api/invoices", json={
            "debtor_id": "canary-debtor-1",
            "currency_code": "USD",
            "payment_terms_type": "NET_30"
        }, headers=headers)
        assert inv_res.status_code == 201, f"Invoice creation failed: {inv_res.text}"
        print("  -> Financial Workflow Processing: PASS")


        # Step 5: Operational Intelligence & Notification Check
        print("[Step 5] Triggering Operational Intelligence Alert...")
        esc_res = await ac.post("/api/notifications/escalations", json={
            "anomaly_title": "Canary Operational Check",
            "severity": "LOW",
            "details": "Routine canary validation check.",
            "channels": ["IN_APP"]
        }, headers=headers)
        assert esc_res.status_code == 201, f"Escalation failed: {esc_res.text}"
        print("  -> Operational Intelligence & Notification: PASS")

        # Step 6: System Resolution Check
        print("[Step 6] Verifying System Status Page...")
        st_res = await ac.get("/api/status")
        assert st_res.status_code == 200, f"Status page failed: {st_res.text}"
        print("  -> Status Resolution: PASS")

    print("=" * 60)
    print("CANARY VALIDATION RESULT: ALL STAGES PASSED CLEANLY")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_canary_validation())
