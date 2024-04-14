import asyncio
from fastapi import Request, APIRouter
from fastapi.responses import StreamingResponse, PlainTextResponse

from lib import Duplex

router = APIRouter()


@router.put("/{identifier}/{file_name}")
async def http_upload(request: Request, identifier: str, file_name: str):
    uid = identifier
    print(f"{uid} - HTTP upload request." )

    file = Duplex.get_file_from_request(request)

    if file.size > 100*1024**2:
        return PlainTextResponse("File too large. 100MiB maximum for HTTP.", status_code=413)

    duplex = Duplex.create_duplex(identifier, file)

    print(f"{uid} - Waiting for client to connect...")
    await duplex.client_connected.wait()

    print(f"{uid} - Client connected. Uploading...")
    await duplex.transfer(request.stream())

    print(f"{uid} - Upload complete.")
    return PlainTextResponse("Transfer complete.", status_code=200)


@router.get("/{identifier}")
async def http_download(identifier: str):
    uid = identifier
    print(f"{uid} - HTTP download request." )

    try:
        duplex = Duplex.get(identifier)
    except KeyError:
        return PlainTextResponse("File not found.", status_code=404)

    print(f"{uid} - Notifying client is connected.")
    duplex.client_connected.set()
    await asyncio.sleep(0.5)

    file_name, file_size, file_type = duplex.get_file_info()

    print(f"{uid} - Starting download.")
    return StreamingResponse(
        duplex.receive(),
        media_type=file_type,
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
    )
