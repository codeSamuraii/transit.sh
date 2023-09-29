from fastapi import FastAPI, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse

from lib.classes import Duplex


app = FastAPI()


@app.put("/curl/{identifier}/{file_name}")
async def upload_file(request: Request, identifier: str):
    duplex = Duplex.from_upload(request)
    file_size, transfered = await duplex.transfer()

    return {"size": file_size, "transfered": transfered}


@app.get("/{identifier}")
async def get_file(identifier: str):
    duplex = Duplex.from_identifer(identifier)
    file_name, file_size, file_type = duplex.get_file_info()

    return StreamingResponse(
        duplex.receive(),
        media_type=file_type,
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": file_size}
    )