import asyncio
import warnings
from json import JSONDecodeError
from fastapi import WebSocket, APIRouter, WebSocketDisconnect, BackgroundTasks
from pydantic import ValidationError

from lib.logging import get_logger
from lib.callbacks import send_error_and_close
from lib.transfer import FileMetadata, FileTransfer

router = APIRouter()
log = get_logger('websockets')


@router.websocket("/send/{uid}")
async def websocket_upload(websocket: WebSocket, uid: str):
    """
    Handles WebSockets file uploads such as those made via the form.

    A JSON header with file metadata should be sent first.
    Then, the client must wait for the signal before sending file chunks.
    """
    await websocket.accept()
    log.debug(f"△ Websocket upload request.")

    try:
        header = await websocket.receive_json()
        file = FileMetadata.get_from_json(header)
    except (JSONDecodeError, KeyError, RuntimeError, ValidationError) as e:
        log.warning("△ Cannot decode file metadata JSON header.", exc_info=e)
        await websocket.send_text("Error: Cannot decode file metadata JSON header.")
        return

    log.info(f"△ Creating transfer: {file}")

    try:
        transfer = await FileTransfer.create(uid, file)
    except KeyError as e:
        log.warning("△ Transfer ID is already used.")
        await websocket.send_text("Error: Transfer ID is already used.")
        return
    except (TypeError, ValidationError) as e:
        log.error("△ Invalid transfer ID or file metadata.", exc_info=e)
        await websocket.send_text("Error: Invalid transfer ID or file metadata.")
        return

    try:
        await transfer.wait_for_client_connected()
    except asyncio.TimeoutError:
        log.warning("△ Receiver did not connect in time.")
        await websocket.send_text(f"Error: Receiver did not connect in time.")
        return

    transfer.info("△ Starting upload...")
    await websocket.send_text("Go for file chunks")

    await transfer.collect_upload(
        stream=websocket.iter_bytes(),
        on_error=send_error_and_close(websocket),
    )

    transfer.info("△ Upload complete.")


@warnings.deprecated(
    "This endpoint is deprecated and will be removed soon. "
    "It should not be used for reference, and it is disabled on the website."
)
@router.websocket("/receive/{uid}")
async def websocket_download(background_tasks: BackgroundTasks, websocket: WebSocket, uid: str):
    await websocket.accept()
    log.debug("▼ Websocket download request.")

    try:
        transfer = await FileTransfer.get(uid)
    except KeyError:
        log.warning("▼ File not found.")
        await websocket.send_text("File not found")
        return

    if await transfer.is_receiver_connected():
        log.warning("▼ A client is already downloading this file.")
        await websocket.send_text("Error: A client is already downloading this file.")
        return

    file_name, file_size, file_type = transfer.get_file_info()
    transfer.debug(f"▼ File: name={file_name}, size={file_size}, type={file_type}")
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

    if not await transfer.set_receiver_connected():
        log.warning("▼ A client is already downloading this file.")
        await websocket.send_text("Error: A client is already downloading this file.")
        return

    transfer.info("▼ Notifying client is connected.")
    await transfer.set_client_connected()
    background_tasks.add_task(transfer.finalize_download)

    transfer.info("▼ Starting download...")
    async for chunk in transfer.supply_download(on_error=send_error_and_close(websocket)):
        await websocket.send_bytes(chunk)
    await websocket.send_bytes(b'')
    transfer.info("▼ Download complete.")
