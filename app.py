import uvloop
import asyncio
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

import os
import logging
import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from views import http_router, ws_router


# Initialize Redis client
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


app = FastAPI(
    debug=True,
    title="Transit.sh",
    description="Direct file transfer with no intermediary storage.",
    version="0.1.0",
    redirect_slashes=False
)

# Remove health checks from logs
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return '"GET /health HTTP/1.1" 200' not in record.getMessage()
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

@app.get("/health")
async def get_health():
    return {"status": "ok"}

@app.on_event("shutdown")
async def shutdown_event():
    await redis_client.close()

app.include_router(http_router)
app.include_router(ws_router)

# Mount local static for HTML
app.mount('/', StaticFiles(directory='static', html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'app:app',
        host='0.0.0.0',
        port=8080,
        workers=4,
        http='httptools',
        ws='websockets',
        loop='uvloop',
        ws_per_message_deflate=False
    )
