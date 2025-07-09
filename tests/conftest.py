import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

# Configure asyncio for pytest
pytest_asyncio.asyncio_mode = "auto"

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment():
    """Set up test environment before each test."""
    # Mock Redis client to avoid needing actual Redis instance
    with patch('app.redis') as mock_redis:
        mock_redis.close = AsyncMock()
        yield mock_redis
