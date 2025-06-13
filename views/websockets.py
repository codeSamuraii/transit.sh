import asyncio
import logging
from fastapi import WebSocket, APIRouter, WebSocketDisconnect

from lib.logging import get_logger
from lib.transfer import FileMetadata, FileTransfer

router = APIRouter()
log = get_logger('websockets')


@router.websocket("/send/{uid}")
async def websocket_upload(websocket: WebSocket, uid: str):
    """
    Upload a file via WebSockets.

    A JSON header with file metadata should be sent first.
    Then, the client must wait for the signal before sending file chunks.
    """
    await websocket.accept()
    log.info(f"△ Websocket upload request." )

    header = await websocket.receive_json()

    try:
        file = FileMetadata.get_file_from_json(header)
        log.info(f"△ File info: name={file.name}, size={file.size}, type={file.content_type}")
    except KeyError as e:
        log.warning(f"△ Invalid header: {header}, error: {e}")
        await websocket.send_text(f"Error: Invalid header - {str(e)}")
        return

    transfer = await FileTransfer.create(uid, file)

    await transfer.wait_for_client_connected()
    await websocket.send_text("Go for file chunks")

    transfer.info("△ Uploading...")
    await transfer.collect_upload(websocket.iter_bytes())
    await asyncio.sleep(2)


@router.websocket("/receive/{uid}")
async def websocket_download(websocket: WebSocket, uid: str):
    await websocket.accept()
    log.info("▼ Websocket download request." )

    try:
        transfer = await FileTransfer.get(uid)
    except KeyError:
        log.warning("▼ File not found.")
        await websocket.send_text("File not found")
        return

    file_name, file_size, file_type = transfer.get_file_info()
    transfer.info(f"▼ File info: name={file_name}, size={file_size}, type={file_type}")
    await websocket.send_json({'file_name': file_name, 'file_size': file_size, 'file_type': file_type})

    transfer.info("▼ Waiting for go-ahead...")
    while True:
        try:
            msg = await websocket.receive_text()
            if msg == "Go for file chunks":
                break
            transfer.warning(f"▼ Unexpected message: {msg}")
        except WebSocketDisconnect:
            transfer.warning("▼ Client disconnected while waiting for go-ahead")
            return

    transfer.info("▼ Notifying client is connected.")
    await transfer.set_client_connected()

    transfer.info("▼ Starting download...")
    async for chunk in transfer.supply_download(protocol='ws'):
        await websocket.send_bytes(chunk)
    await websocket.send_bytes(b'')
