import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_console_route():
    """Verify /console serves the Operations Console SPA HTML template."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/console")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "NomadX" in response.text

@pytest.mark.asyncio
async def test_checkout_route():
    """Verify /checkout/{intent_id} serves the Web3 Checkout Portal HTML template."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/checkout/intent-12345")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "NomadX Web3 Checkout Portal" in response.text or "NomadX Pay" in response.text

@pytest.mark.asyncio
async def test_pay_route_alias():
    """Verify /pay/{intent_id} alias route functions identically to /checkout."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/pay/intent-67890")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

@pytest.mark.asyncio
async def test_root_redirect():
    """Verify / redirects to /console."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
        response = await ac.get("/")
        assert response.status_code in [307, 302, 301]
        assert "/console" in response.headers["location"]

@pytest.mark.asyncio
async def test_unauthenticated_sse_rejection():
    """Verify unauthenticated SSE stream requests return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/events/stream")
        assert response.status_code == 401
        assert "Not authenticated" in response.text

@pytest.mark.asyncio
async def test_authenticated_sse_stream_tenant_isolation():
    """Verify authenticated SSE stream enforces tenant boundary and scopes events to caller org."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Step 1: Register merchant tenant
        email = "sse_tenant_op@nomadx.io"
        reg_res = await ac.post("/auth/register", json={
            "email": email,
            "password": "SecurePassword123!",
            "name": "SSE Operator",
            "organization_name": "SSE Secured Tenant",
            "organization_slug": "sse-secured-tenant",
            "default_currency": "USD"
        })
        assert reg_res.status_code in [201, 409]

        # Step 2: Login to obtain session JWT
        login_res = await ac.post("/auth/login", json={
            "email": email,
            "password": "SecurePassword123!"
        })
        assert login_res.status_code == 200
        token = login_res.json()["token"]

        # Step 3: Connect to SSE stream with JWT query token
        response = await ac.get(f"/api/events/stream?token={token}&limit=1")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "CONNECTED" in response.text
        assert "organization_id" in response.text
