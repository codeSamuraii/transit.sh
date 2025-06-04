import asyncio
from fastapi import WebSocket, APIRouter, WebSocketDisconnect

from lib import FileTransfer


router = APIRouter()


@router.websocket("/send/{uid}")
async def websocket_upload(websocket: WebSocket, uid: str):
    """
    Upload a file via WebSockets.

    A JSON header with file metadata should be sent first.
    Then, the client must wait for the signal before sending file chunks.
    """
    await websocket.accept()
    print(f"⇑ {uid} ⇑ - Websocket upload request." )

    header = await websocket.receive_json()

    try:
        file = FileTransfer.get_file_from_header(header)
        print(f"⇑ {uid} ⇑ - File info: name={file.name}, size={file.size}, type={file.content_type}")
    except KeyError as e:
        print(f"⇑ {uid} ⇑ - Invalid header: {header}, error: {e}")
        await websocket.send_text(f"Error: Invalid header - {str(e)}")
        return

    transfer = FileTransfer.create_transfer(uid, file)

    await transfer.client_connected.wait()
    print(f"⇑ {uid} ⇑ - Client connected, signaling to start sending chunks")
    await websocket.send_text("Go for file chunks")

    print(f"⇑ {uid} ⇑ - Starting upload...")
    await transfer.transfer(websocket.iter_bytes())


@router.websocket("/receive/{uid}")
async def websocket_download(websocket: WebSocket, uid: str):
    await websocket.accept()
    print(f"⇓ {uid} ⇓ - Websocket download request." )

    try:
        transfer = FileTransfer.get(uid)
    except KeyError:
        print(f"⇓ {uid} ⇓ - File not found.")
        await websocket.send_text("File not found")
        return

    file_name, file_size, file_type = transfer.get_file_info()
    print(f"⇓ {uid} ⇓ - File info: name={file_name}, size={file_size}, type={file_type}")
    await websocket.send_json({'file_name': file_name, 'file_size': file_size, 'file_type': file_type})

    print(f"⇓ {uid} ⇓ - Waiting for go-ahead...")
    while True:
        try:
            msg = await websocket.receive_text()
            if msg == "Go for file chunks":
                break
            print(f"⇓ {uid} ⇓ - Unexpected message: {msg}")
        except WebSocketDisconnect:
            print(f"⇓ {uid} ⇓ - Client disconnected while waiting for go-ahead")
            return

    print(f"⇓ {uid} ⇓ - Notifying client is connected.")
    transfer.client_connected.set()
    await asyncio.sleep(0.5)

    print(f"⇓ {uid} ⇓ - Starting download...")
    try:
        async for chunk in transfer.receive():
            await websocket.send_bytes(chunk)
        await websocket.send_bytes(b'')
        print(f"⇓ {uid} ⇓ - Download complete.")
    except WebSocketDisconnect:
        print(f"⇓ {uid} ⇓ - Client disconnected during download")
    except Exception as e:
        print(f"⇓ {uid} ⇓ - Error during download: {str(e)}")
