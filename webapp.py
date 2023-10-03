from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse

from lib.classes import Duplex


app = FastAPI()


@app.get('/')
async def index():
    return FileResponse('static/index.html')


@app.get("/health")
async def get_health():
    return {"status": "ok"}


@app.put("/{identifier}/{file_name}")
async def upload_file(request: Request, identifier: str):
    duplex = Duplex.from_upload(request)
    transfered, file_size = await duplex.transfer()

    return {"size": file_size, "transfered": transfered}


@app.get("/{identifier}")
async def get_file(identifier: str):
    duplex = Duplex.from_identifer(identifier)
    file_name, file_size, file_type = duplex.get_file_info()

    return StreamingResponse(
        duplex.receive(),
        media_type=file_type,
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
    )

 
app.mount('/static', StaticFiles(directory='static', html=True), name='static')