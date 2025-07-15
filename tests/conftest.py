import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def setup_test_environment():
    """Set up test environment before each test."""
    # Mock Redis client to avoid needing actual Redis instance
    with patch('app.redis') as mock_redis:
        mock_redis.close = AsyncMock()
        yield mock_redis
