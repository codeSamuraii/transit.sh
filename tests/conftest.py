import os
import sys
import time
import httpx
import pytest
import socket
import subprocess
import redis as redis_client
from typing import AsyncIterator

from tests.ws_client import WebSocketTestClient
from lib.logging import get_logger
log = get_logger('setup-tests')


def find_free_port():
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session")
def anyio_backend():
    return 'asyncio'


@pytest.fixture(scope="session")
def live_server():
    """Start uvicorn server in a subprocess."""
    port = find_free_port()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    processes = {}

    try:
        print()
        log.debug(f"Starting test server...")
        redis_proc = subprocess.Popen(
            ['redis-server', '--port', '6379', '--save', '', '--appendonly', 'no'],
            cwd=project_root,
            stdout=subprocess.DEVNULL
        )
        processes['redis'] = redis_proc

        try:
            time.sleep(2)
            redis_client.from_url('redis://127.0.0.1:6379').ping()
        except redis_client.ConnectionError:
            log.error("Failed to connect to Redis server. Ensure Redis is running.")
            raise

        uvicorn_proc = subprocess.Popen(
            ['uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(port)],
            cwd=project_root
        )
        processes['uvicorn'] = uvicorn_proc

        base_url = f'127.0.0.1:{port}'
        max_retries = 5
        for i in range(max_retries):
            try:
                response = httpx.get(f'http://{base_url}/health', timeout=5)
                if response.status_code == 200:
                    break
            except Exception as e:
                if i == max_retries - 1:
                    uvicorn_proc.terminate()
                    raise RuntimeError(f"Server failed to start after {max_retries} attempts") from None

            time.sleep(2.0)

        log.debug(f"Server started at {base_url}")
        print()
        yield base_url

        print()
        for name, process in sorted(processes.items(), key=lambda x: -ord(x[0][0])):
            if process.poll() is None:
                log.debug(f"Terminating {name} process")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired: pass

    finally:
        for name, process in processes.items():
            if process.poll() is None:
                log.warning(f"Forcefully terminating {name}")
                process.kill()


@pytest.fixture
async def test_client(live_server: str) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client for testing."""
    async with httpx.AsyncClient(base_url=f'http://{live_server}') as client:
        print()
        yield client


@pytest.fixture
async def websocket_client(live_server: str):
    """WebSocket client for testing."""
    base_ws_url = f'ws://{live_server}'
    return WebSocketTestClient(base_ws_url)


@pytest.mark.anyio
async def test_mocks(test_client: httpx.AsyncClient) -> None:
    response = await test_client.get("/nonexistent-endpoint")
    assert response.status_code == 404, "Expected 404 for nonexistent endpoint"

