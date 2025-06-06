import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from uvicorn.logging import DefaultFormatter

from views import http_router, ws_router


app = FastAPI()

# Remove health checks from logs
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host='0.0.0.0',
        port=8080,
        loop='uvloop',
        http='httptools',
        ws='websockets',
        workers=1,
    )
