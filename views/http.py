import asyncio
from fastapi import Request, APIRouter
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse

from lib import FileTransfer

router = APIRouter()


@router.put("/{uid}")
@router.put("/{uid}/{filename}")
async def http_upload(request: Request, uid: str, filename: str | None = None):
    """
    Upload a file via HTTP PUT.

    The filename can be provided in two ways:
    - As a path parameter: `/{uid}/{filename}` (automatically used by `curl --upload-file`)
    - As a query parameter: `/{uid}?filename={filename}`

    File size is limited to 100 MiB for HTTP transfers.
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required as path parameter or query parameter")

    print(f"⇑ {uid} ⇑ - HTTP upload request: {filename}" )
    file = FileTransfer.get_file_from_request(request)

    if file.size > 100*1024**2:
        return PlainTextResponse("File too large. 100MiB maximum for HTTP.", status_code=413)

    transfer = FileTransfer.create_transfer(uid, file)

    print(f"⇑ {uid} ⇑ - Waiting for client to connect...")
    await transfer.client_connected.wait()

    print(f"⇑ {uid} ⇑ - Client connected. Uploading...")
    await transfer.transfer(request.stream())

    print(f"⇑ {uid} ⇑ - Upload complete.")
    return PlainTextResponse("Transfer complete.", status_code=200)


@router.get("/{uid}")
async def http_download(uid: str):
    """
    Download a file via HTTP GET.

    The uid is used to identify the file to download.
    File chunks are forwarded from sender to receiver via streaming.
    """
    if '.' in uid or '/' in uid:
        return PlainTextResponse("Invalid request.", status_code=400)

    try:
        transfer = FileTransfer.get(uid)
        print(f"⇓ {uid} ⇓ - HTTP download request." )
    except KeyError:
        return PlainTextResponse("File not found.", status_code=404)

    print(f"⇓ {uid} ⇓ - Notifying client is connected.")
    transfer.client_connected.set()
    await asyncio.sleep(0.5)

    file_name, file_size, file_type = transfer.get_file_info()

    print(f"⇓ {uid} ⇓ - Starting download of {file_name} ({file_size} bytes, type: {file_type})")
    return StreamingResponse(
        transfer.receive(),
        media_type=file_type,
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
    )
