import pytest
from httpx import ASGITransport, AsyncClient

from repopilot_api.main import app


@pytest.mark.anyio
async def test_health_returns_service_status() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "repopilot-api"}
