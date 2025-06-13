import os
import redis
import logging
import redis.asyncio
import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from views import http_router, ws_router
from lib.logging import setup_logging


# Sentry
if sentry_dsn := os.getenv("SENTRY_DSN", ""):
    sentry_sdk.init(
        dsn=sentry_dsn,
        send_default_pii=True,
    )

# Redis
redis_client = redis.asyncio.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield
    await redis_client.close()

# FastAPI
app = FastAPI(
    debug=True,
    title="Transit.sh",
    description="Direct file transfer with no intermediary storage.",
    version="0.1.0",
    redirect_slashes=True,
    lifespan=lifespan
)

# Health checks
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return '"GET /health HTTP/1.1" 200' not in record.getMessage()
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

@app.get("/health")
async def get_health():
    return {"status": "ok"}

# Indexing
@app.get("/robots.txt")
async def get_robots_txt():
    return FileResponse("static/robots.txt", content_disposition_type="inline")

# App. routes
app.include_router(http_router)
app.include_router(ws_router)

# Static files
app.mount('/', StaticFiles(directory='static', html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'app:app',
        host='0.0.0.0',
        port=8080,
        workers=1,
        http='httptools',
        ws='websockets',
        loop='uvloop',
        ws_per_message_deflate=False,
    )
