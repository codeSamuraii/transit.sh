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
        await transfer.wait_for_client_connected()
    except TimeoutError:
        log.warning("△ Receiver did not connect in time.")
        await websocket.send_text(f"Error: Receiver did not connect in time.")
        return
    except Exception as e:
        log.error("△ Error while waiting for receiver connection.", exc_info=e)
        await websocket.send_text("Error: Error while waiting for receiver connection.")
        return

    transfer.debug("△ Sending go-ahead...")
    await websocket.send_text("Go for file chunks")

    transfer.info("△ Starting upload...")
    await transfer.collect_upload(
        stream=websocket.iter_bytes(),
        on_error=send_error_and_close(websocket),
    )

    transfer.info("△ Upload complete.")
