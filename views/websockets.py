import string
import warnings
from pydantic import ValidationError
from fastapi import WebSocket, APIRouter, WebSocketDisconnect, BackgroundTasks

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
    if any(char not in string.ascii_letters + string.digits + '-' for char in uid):
        log.debug(f"△ Invalid transfer ID.")
        await websocket.close(code=1008, reason="Invalid transfer ID")
        return

    await websocket.accept()
    log.debug(f"△ Websocket upload request.")

    try:
        header = await websocket.receive_json()
        file = FileMetadata.get_from_json(header)

    except ValidationError as e:
        log.warning("△ Invalid file metadata JSON header.", exc_info=e)
        await websocket.send_text("Error: Invalid file metadata JSON header.")
        return
    except Exception as e:
        log.error("△ Cannot decode file metadata JSON header.", exc_info=e)
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
        await transfer.wait_for_receiver()
    except TimeoutError:
        log.warning("△ Receiver timeout")
        await websocket.send_text("Error: Receiver timeout")
        return

    transfer.debug("△ Sending go-ahead...")
    await websocket.send_text("Go for file chunks")

    transfer.info("△ Starting upload...")
    await transfer.consume_upload(
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

    progress = await transfer.store.get_progress()
    file_name, file_size, file_type = transfer.get_file_info()
    
    metadata = {'file_name': file_name, 'file_size': file_size, 'file_type': file_type}
    if progress > 0:
        metadata['resume_from'] = progress
        transfer.info(f"▼ Resuming from byte {progress}")
    
    await websocket.send_json(metadata)

    transfer.info("▼ Waiting for go-ahead...")
    while True:
        try:
            msg = await websocket.receive_text()
            if msg == "Go for file chunks":
                break
        except WebSocketDisconnect:
            transfer.warning("▼ Disconnected while waiting")
            return

    await transfer.notify_receiver_connected()
    background_tasks.add_task(transfer.finalize_download)

    transfer.info("▼ Starting download")
    async for chunk in transfer.produce_download(on_error=send_error_and_close(websocket)):
        await websocket.send_bytes(chunk)
    await websocket.send_bytes(b'')
    transfer.info("▼ Download complete")
