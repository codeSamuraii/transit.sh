import httpx
import pytest
import fakeredis
from typing import AsyncIterator
from redis import asyncio as redis
from unittest.mock import AsyncMock, patch
from starlette.testclient import TestClient

from app import app
from lib.store import Store


@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
async def redis_client() -> AsyncIterator[redis.Redis]:
    async with fakeredis.FakeAsyncRedis() as client:
        yield client

@pytest.fixture
async def test_client(redis_client: redis.Redis) -> AsyncIterator[httpx.AsyncClient]:
    def get_redis_override(*args, **kwargs) -> redis.Redis:
        return redis_client

    # Make sure the app has the Redis state set up
    app.state.redis = redis_client

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Patch the `get_redis` method of the `Store` class
        with patch.object(Store, 'get_redis', new=get_redis_override):
            print("")
            yield client

@pytest.fixture
async def websocket_client(redis_client: redis.Redis):
    """Alternative WebSocket client using Starlette TestClient."""
    def get_redis_override(*args, **kwargs) -> redis.Redis:
        return redis_client

    # Make sure the app has the Redis state set up
    app.state.redis = redis_client

    # Patch the `get_redis` method of the `Store` class
    with patch.object(Store, 'get_redis', new=get_redis_override):
        with TestClient(app, base_url="http://testserver") as client:
            print("")
            yield client

@pytest.mark.anyio
async def test_mocks(test_client: httpx.AsyncClient) -> None:
    response = await test_client.get("/nonexistent-endpoint")
    assert response.status_code == 404, "Expected 404 for nonexistent endpoint"
