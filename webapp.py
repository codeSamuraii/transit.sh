import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from uvicorn.logging import DefaultFormatter

from views import http_router, ws_router


app = FastAPI()

class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return '"GET /health HTTP/1.1" 200' not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

@app.get("/health")
async def get_health():
    return {"status": "ok"}

app.include_router(http_router)
app.include_router(ws_router)

# Mount local static for HTML
app.mount('/', StaticFiles(directory='static', html=True), name='static')
