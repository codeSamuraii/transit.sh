from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse

from lib.classes import Duplex


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


@app.put("/{identifier}/{file_name}")
async def upload_file(request: Request, identifier: str, file_name: str):
    duplex = Duplex.from_upload(request)
    transfered, file_size = await duplex.transfer()

    return {"size": file_size, "transfered": transfered}


@app.get("/{identifier}")
async def get_file(identifier: str):
    try:
        duplex = Duplex.from_identifer(identifier)
        file_name, file_size, file_type = duplex.get_file_info()
    except KeyError:
        return PlainTextResponse("File not found", status_code=404)
    else:
        return StreamingResponse(
            duplex.receive(),
            media_type=file_type,
            headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
        )


# Mount local static directory for HTML
app.mount('/static', StaticFiles(directory='static', html=True), name='static')

# Mount remote disk if present or local static for CSS
if Path('/extra').exists():
    app.mount('/css', StaticFiles(directory='/extra'), name='css')
else:
    app.mount('/css', StaticFiles(directory='static'), name='css')
