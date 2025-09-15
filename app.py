import os
import sentry_sdk
import redis.asyncio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from lib.logging import setup_logging
from views import http_router, ws_router, misc_router


# FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    sentry_sdk.init(release=os.getenv('DEPLOYMENT_ID', 'local'))
    app.state.redis = redis.asyncio.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379'))
    yield
    await app.state.redis.aclose()

app = FastAPI(
    title="Transit.sh",
    description="Direct file transfer without intermediate storage.",
    version="0.1.0",
    redirect_slashes=True,
    lifespan=lifespan
)

# App. routes
app.include_router(misc_router)
app.include_router(ws_router)  # WebSocket routes first to avoid conflicts
app.include_router(http_router)

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
