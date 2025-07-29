import os
import sys
import asyncio
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

import aiohttp
import picows
import pytest
import pytest_asyncio


class TransitIntegrationClient:
    """Integration test client for Transit.sh with real Redis and uvicorn processes."""
    
    def __init__(self, base_url: str = "http://localhost:8080", ws_base_url: str = "ws://localhost:8080"):
        self.base_url = base_url
        self.ws_base_url = ws_base_url
        self.http_session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.http_session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.http_session:
            await self.http_session.close()
    
    # HTTP Methods
    async def get(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        """Make a GET request."""
        url = f"{self.base_url}{path}"
        return await self.http_session.get(url, **kwargs)
    
    async def put(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        """Make a PUT request."""
        url = f"{self.base_url}{path}"
        return await self.http_session.put(url, **kwargs)
    
    async def post(self, path: str, **kwargs) -> aiohttp.ClientResponse:
        """Make a POST request."""
        url = f"{self.base_url}{path}"
        return await self.http_session.post(url, **kwargs)
    
    # WebSocket Methods
    @asynccontextmanager
    async def websocket(self, path: str):
        """Create a WebSocket connection using picows."""
        url = f"{self.ws_base_url}{path}"
        
        ws_client = await picows.ws_connect(picows.WSTransport, url)
        try:
            yield WebSocketWrapper(ws_client)
        finally:
            await ws_client.close()


class WebSocketWrapper:
    """Wrapper around picows WebSocket to provide a simpler API."""
    
    def __init__(self, ws: picows.WSTransport):
        self._ws = ws
        
    async def send_text(self, data: str):
        """Send text data."""
        await self._ws.send(picows.WSMsgType.TEXT, data.encode())
    
    async def send_bytes(self, data: bytes):
        """Send binary data."""
        await self._ws.send(picows.WSMsgType.BINARY, data)
    
    async def send_json(self, data: Dict[str, Any]):
        """Send JSON data."""
        import json
        await self.send_text(json.dumps(data))
    
    async def receive(self) -> tuple[picows.WSMsgType, bytes]:
        """Receive raw message."""
        return await self._ws.recv()
    
    async def receive_text(self) -> str:
        """Receive text message."""
        msg_type, data = await self.receive()
        if msg_type != picows.WSMsgType.TEXT:
            raise ValueError(f"Expected text message, got {msg_type}")
        return data.decode()
    
    async def receive_bytes(self) -> bytes:
        """Receive binary message."""
        msg_type, data = await self.receive()
        if msg_type != picows.WSMsgType.BINARY:
            raise ValueError(f"Expected binary message, got {msg_type}")
        return data
    
    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON message."""
        import json
        text = await self.receive_text()
        return json.loads(text)
    
    async def close(self):
        """Close the WebSocket connection."""
        await self._ws.close()


def find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


async def wait_for_port(host: str, port: int, timeout: float = 30.0):
    """Wait for a service to start listening on a port."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Service on {host}:{port} did not start within {timeout} seconds")


async def wait_for_redis(redis_port: int, timeout: float = 30.0):
    """Wait for Redis to be ready by attempting to connect."""
    import redis.asyncio as redis
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            client = redis.Redis(host='localhost', port=redis_port)
            await client.ping()
            await client.aclose()
            return True
        except (ConnectionRefusedError, redis.ConnectionError):
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Redis on port {redis_port} did not start within {timeout} seconds")


class ServerProcessManager:
    """Manages Redis and uvicorn server processes."""
    
    def __init__(self):
        self.redis_process: Optional[subprocess.Popen] = None
        self.uvicorn_process: Optional[subprocess.Popen] = None
        self.redis_port: Optional[int] = None
        self.uvicorn_port: Optional[int] = None
        
    async def start_redis(self) -> int:
        """Start Redis server and return its port."""
        self.redis_port = find_free_port()
        
        # Start Redis with minimal config
        self.redis_process = subprocess.Popen(
            [
                'redis-server',
                '--port', str(self.redis_port),
                '--save', '',  # Disable persistence
                '--appendonly', 'no',  # Disable AOF
                '--loglevel', 'warning'
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for Redis to be ready
        await wait_for_redis(self.redis_port)
        return self.redis_port
    
    async def start_uvicorn(self, redis_url: str) -> int:
        """Start uvicorn server and return its port."""
        self.uvicorn_port = find_free_port()
        
        # Set environment for the uvicorn process
        env = os.environ.copy()
        env['REDIS_URL'] = redis_url
        
        # Start uvicorn
        self.uvicorn_process = subprocess.Popen(
            [
                sys.executable, '-m', 'uvicorn',
                'app:app',
                '--host', '0.0.0.0',
                '--port', str(self.uvicorn_port),
                '--workers', '1',
                '--loop', 'uvloop',
                '--ws', 'websockets',
                '--log-level', 'warning'
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(os.path.abspath(__file__))  # Ensure we're in the project root
        )
        
        # Wait for uvicorn to be ready
        await wait_for_port('localhost', self.uvicorn_port)
        return self.uvicorn_port
    
    def terminate_process(self, process: subprocess.Popen, name: str):
        """Terminate a process gracefully."""
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print(f"Warning: {name} did not terminate gracefully, killing...")
                process.kill()
                process.wait()
    
    def cleanup(self):
        """Clean up all processes."""
        self.terminate_process(self.redis_process, "Redis")
        self.terminate_process(self.uvicorn_process, "uvicorn")


@asynccontextmanager
async def transit_test_servers():
    """Context manager to start and stop Redis and uvicorn servers."""
    manager = ServerProcessManager()
    
    try:
        # Start Redis
        redis_port = await manager.start_redis()
        redis_url = f"redis://localhost:{redis_port}"
        print(f"Started Redis on port {redis_port}")
        
        # Start uvicorn
        uvicorn_port = await manager.start_uvicorn(redis_url)
        print(f"Started uvicorn on port {uvicorn_port}")
        
        # Create and yield the test client
        base_url = f"http://localhost:{uvicorn_port}"
        ws_base_url = f"ws://localhost:{uvicorn_port}"
        
        async with TransitIntegrationClient(base_url, ws_base_url) as client:
            yield client
            
    finally:
        # Clean up processes
        manager.cleanup()
        print("Cleaned up test servers")


# Pytest fixtures
@pytest_asyncio.fixture(scope="session")
async def integration_client():
    """Session-scoped fixture that provides the integration test client."""
    async with transit_test_servers() as client:
        yield client


# Example usage in tests
@pytest.mark.asyncio
async def test_health_endpoint(integration_client: TransitIntegrationClient):
    """Test the health endpoint with real servers."""
    response = await integration_client.get("/health")
    assert response.status == 200
    data = await response.json()
    assert data == {"status": "ok"}


@pytest.mark.asyncio
async def test_websocket_upload_integration(integration_client: TransitIntegrationClient):
    """Test WebSocket upload with real servers."""
    transfer_id = "test-integration-transfer"
    
    async with integration_client.websocket(f"/send/{transfer_id}") as ws:
        # Send file metadata
        await ws.send_json({
            'file_name': 'test.txt',
            'file_size': 11,
            'file_type': 'text/plain'
        })
        
        # In a real test, you'd have a receiver connect here
        # For this example, we'll just verify the connection works
        
        # The actual test would timeout waiting for receiver
        # This is just to show the client works


@pytest.mark.asyncio  
async def test_http_download_not_found(integration_client: TransitIntegrationClient):
    """Test HTTP download for non-existent transfer."""
    response = await integration_client.get("/nonexistent-transfer")
    assert response.status == 404
    data = await response.json()
    assert "not found" in data["detail"].lower()


# Requirements to add to your test dependencies:
# aiohttp>=3.9.0
# picows>=1.0.0
# pytest-asyncio>=0.21.0