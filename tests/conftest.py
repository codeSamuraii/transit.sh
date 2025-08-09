import os
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


@pytest.fixture(scope="session")
def anyio_backend():
    return 'asyncio'


def find_free_port():
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def is_redis_reachable(tries: int = 5, delay: float = 1.0) -> bool:
    """Check if Redis server is reachable."""
    for _ in range(tries):
        try:
            redis_client.from_url('redis://127.0.0.1:6379').ping()
            return True
        except redis_client.ConnectionError:
            time.sleep(delay)
    return False

def is_uvicorn_reachable(port: int, tries: int = 5, delay: float = 1.0, *, base_url: str = 'http://127.0.0.1') -> bool:
    """Check if Uvicorn server is reachable."""
    for _ in range(tries):
        try:
            response = httpx.get(f'{base_url}:{port}/health', timeout=delay * 0.8)
            if response.status_code == 200:
                return True
        except httpx.RequestError:
            time.sleep(delay)
    return False

def start_redis_server(project_root: str) -> None:
    print('\n', end='\r')
    log.debug("- Starting Redis server...")

    try:
        redis_proc = subprocess.Popen(
            ['redis-server', '--port', '6379', '--save', '', '--appendonly', 'no'],
            cwd=project_root,
            stdout=subprocess.DEVNULL
        )

        if not is_redis_reachable():
            log.error("x Redis server did not start successfully.")
            redis_proc.terminate()
            raise RuntimeError("Could not start Redis server for tests.") from None

        return redis_proc

    except Exception as e:
            log.error("x Failed to start Redis server.", exc_info=e)
            redis_proc.kill()
            raise RuntimeError("Could not start Redis server for tests.") from e

def start_uvicorn_server(port: int, project_root: str) -> None:
    try:
        log.debug("- Starting uvicorn server...")
        uvicorn_proc = subprocess.Popen(
            ['uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(port)],
            cwd=project_root
        )

        if not is_uvicorn_reachable(port):
            log.error("x Uvicorn server did not start successfully.")
            uvicorn_proc.terminate()
            raise RuntimeError("Could not start Uvicorn server for tests.") from None

        return uvicorn_proc

    except Exception as e:
        log.error("x Failed to start Uvicorn server.", exc_info=e)
        uvicorn_proc.kill()
        raise RuntimeError("Could not start Uvicorn server for tests.") from e

@pytest.fixture(scope="session")
def live_server():
    """Start uvicorn server in a subprocess."""
    port = find_free_port()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    processes = {}

    if not is_redis_reachable(tries=1):
        redis_proc = start_redis_server(project_root)
        processes['redis'] = redis_proc

    if not is_uvicorn_reachable(port, tries=1):
        uvicorn_proc = start_uvicorn_server(port, project_root)
        processes['uvicorn'] = uvicorn_proc

    yield f'127.0.0.1:{port}'

    print()
    for name in ['uvicorn', 'redis']:
        process = processes.get(name)
        if not process or process.poll() is not None:
            continue

        log.debug(f"- Terminating {name} process")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning(f"- {name} process did not terminate in time, killing it")
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

