from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from views import http_router, ws_router


app = FastAPI()


@app.get('/')
async def index():
    return FileResponse('static/index.html')


@app.get('/robots.txt')
async def robots():
    return FileResponse('static/robots.txt')


@app.get("/health")
async def get_health():
    return {"status": "ok"}


app.include_router(http_router)
app.include_router(ws_router)


# Mount local static for HTML
app.mount('/static', StaticFiles(directory='static', html=True), name='static')
