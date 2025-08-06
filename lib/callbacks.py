from fastapi import HTTPException
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketState
from typing import Awaitable, Callable


def send_error_and_close(websocket: WebSocket) -> Callable[[Exception | str], Awaitable[None]]:
    """Callback to send an error message and close the WebSocket connection."""

    async def _send_error_and_close(error: Exception | str) -> None:
        message = str(error) if isinstance(error, Exception) else error
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(f"Error: {message}")
            await websocket.close(code=1011, reason=message)

    return _send_error_and_close


class StreamTerminated(Exception):
    """Raised to terminate a stream when an error occurs after the response has started."""
    pass

def raise_http_exception(request: Request) -> Callable[[Exception | str], Awaitable[None]]:
    """Callback to raise an HTTPException with a specific status code."""

    async def _raise_http_exception(error: Exception | str) -> None:
        message = str(error) if isinstance(error, Exception) else error
        code = error.status_code if isinstance(error, HTTPException) else 400
        raise StreamTerminated(f"{code}: {message}") from error

    return _raise_http_exception
