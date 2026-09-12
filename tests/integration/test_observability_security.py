import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_slo_baselines_endpoint():
    """Verify /api/metrics/slo-baselines returns valid JSON metrics."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/metrics/slo-baselines")
        assert response.status_code == 200
        data = response.json()
        assert data["availability_percentage"] > 99.0
        assert data["api_latency_p50_ms"] > 0

@pytest.mark.asyncio
async def test_prometheus_metrics_endpoint():
    """Verify /api/metrics returns standard Prometheus format text output."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "nomadx_http_requests_total" in response.text
        assert "nomadx_availability_ratio" in response.text

@pytest.mark.asyncio
async def test_prometheus_alias_endpoint():
    """Verify /api/metrics/prometheus alias endpoint returns Prometheus text output."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/metrics/prometheus")
        assert response.status_code == 200
        assert "nomadx_worker_tasks_processed_total" in response.text
