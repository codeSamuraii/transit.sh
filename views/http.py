import asyncio
from fastapi import Request, APIRouter
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse

from lib import FileTransfer

router = APIRouter()


@router.put("/{identifier}")
@router.put("/{identifier}/{filename}")
async def http_upload(request: Request, identifier: str, filename: str | None = None):
    """
    Upload a file via HTTP PUT.

    The filename can be provided in two ways:
    - As a path parameter: `/{identifier}/{filename}` (automatically used by `curl --upload-file`)
    - As a query parameter: `/{identifier}?filename={filename}`

    File size is limited to 100 MiB for HTTP transfers.
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required as path parameter or query parameter")

    print(f"⇑ {identifier} - HTTP upload request: {filename}" )
    file = FileTransfer.get_file_from_request(request)

    if file.size > 100*1024**2:
        return PlainTextResponse("File too large. 100MiB maximum for HTTP.", status_code=413)

    transfer = FileTransfer.create_transfer(identifier, file)

    print(f"⇑ {identifier} - Waiting for client to connect...")
    await transfer.client_connected.wait()

    print(f"⇑ {identifier} - Client connected. Uploading...")
    await transfer.transfer(request.stream())

    print(f"⇑ {identifier} - Upload complete.")
    return PlainTextResponse("Transfer complete.", status_code=200)


@router.get("/{identifier}")
async def http_download(identifier: str):
    """
    Download a file via HTTP GET.

    The identifier is used to identify the file to download.
    File chunks are forwarded from sender to receiver via streaming.
    """
    if '.' in identifier or '/' in identifier:
        return PlainTextResponse("Invalid request.", status_code=400)

    try:
        transfer = FileTransfer.get(identifier)
        print(f"⇓ {identifier} - HTTP download request." )
    except KeyError:
        return PlainTextResponse("File not found.", status_code=404)

    print(f"⇓ {identifier} - Notifying client is connected.")
    transfer.client_connected.set()
    await asyncio.sleep(0.5)

    file_name, file_size, file_type = transfer.get_file_info()

    print(f"⇓ {identifier} - Starting download.")
    return StreamingResponse(
        transfer.receive(),
        media_type=file_type,
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
    )
