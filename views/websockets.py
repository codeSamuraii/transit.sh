import asyncio
from fastapi import WebSocket, APIRouter

from lib import Duplex


router = APIRouter()


@router.websocket("/send/{identifier}")
async def websocket_upload(websocket: WebSocket, identifier: str):
    uid = identifier
    await websocket.accept()
    print(f"{uid} - Websocket upload request." )

    header = await websocket.receive_json()

    try:
        file = Duplex.get_file_from_header(header)
    except KeyError:
        print(f"{uid} - Invalid header: {header}")
        return

    duplex = Duplex.create_duplex(uid, file)

    await duplex.client_connected.wait()
    await websocket.send_text("Go for file chunks")

    print(f"{uid} - Starting upload...")
    await duplex.transfer(websocket.iter_bytes())

    print(f"{uid} - Upload complete.")


@router.websocket("/receive/{identifier}")
async def websocket_download(websocket: WebSocket, identifier: str):
    uid = identifier
    await websocket.accept()
    print(f"{uid} - Websocket download request." )

    try:
        duplex = Duplex.get(identifier)
    except KeyError:
        print(f"{uid} - File not found.")
        await websocket.send_text("File not found")
        return

    file_name, file_size, file_type = duplex.get_file_info()
    await websocket.send_json({'file_name': file_name, 'file_size': file_size, 'file_type': file_type})

    print(f"{uid} - Waiting for go-ahead...")
    while (msg := await websocket.receive_text()) != "Go for file chunks":
        print(f"{uid} - Unexpected message: {msg}")

    print(f"{uid} - Notifying client is connected.")
    duplex.client_connected.set()
    await asyncio.sleep(0.5)

    print(f"{uid} - Starting download...")
    async for chunk in duplex.receive():
        await websocket.send_bytes(chunk)
    await websocket.send_bytes(b'')
    print(f"{uid} - Download complete.")
