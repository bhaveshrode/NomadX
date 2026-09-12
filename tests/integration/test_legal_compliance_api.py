import pytest
from httpx import AsyncClient, ASGITransport
from nomadx.apps.api.main import app

@pytest.mark.asyncio
async def test_legal_compliance_policies_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/legal/policies")
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == "1.0.0"
        policies = data["policies"]
        assert "terms_of_service" in policies
        assert "privacy_policy" in policies
        assert "data_processing" in policies
        assert "acceptable_use" in policies
        assert "financial_compliance" in policies

        assert "Acceptance of Terms" in policies["terms_of_service"]["content"]
        assert "Data Collection" in policies["privacy_policy"]["content"]
