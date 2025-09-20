import string
import warnings
from pydantic import ValidationError
from fastapi import WebSocket, APIRouter, WebSocketDisconnect, BackgroundTasks

from lib.logging import get_logger
from lib.callbacks import send_error_and_close
from lib.transfer import FileMetadata, FileTransfer
from lib.resume import ResumptionHandler

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


@router.websocket("/resume/{uid}")
async def websocket_resume_upload(websocket: WebSocket, uid: str):
    """
    Resume an interrupted WebSocket upload.
    Sends the byte position to resume from to the client.
    """
    if any(char not in string.ascii_letters + string.digits + '-' for char in uid):
        log.debug(f"△ Invalid transfer ID.")
        await websocket.close(code=1008, reason="Invalid transfer ID")
        return

    await websocket.accept()
    log.debug(f"△ Resume upload request for {uid}")

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

    try:
        transfer = await FileTransfer.get(uid)
        handler = ResumptionHandler(uid, transfer.store)

        if not await handler.can_resume_upload():
            log.warning("△ Transfer cannot be resumed.")
            await websocket.send_text("Error: Transfer cannot be resumed or does not exist.")
            return

        if not await handler.validate_resume_request(file):
            log.warning("△ Resume request does not match original transfer.")
            await websocket.send_text("Error: File metadata does not match original transfer.")
            return

        resume_info = await handler.prepare_upload_resume()
        resume_from = resume_info['resume_from']

        log.info(f"△ Resuming transfer from byte {resume_from}: {file}")

    except KeyError:
        log.warning("△ Transfer not found for resumption.")
        await websocket.send_text("Error: Transfer not found.")
        return
    except Exception as e:
        log.error("△ Error preparing resume.", exc_info=e)
        await websocket.send_text(f"Error: {str(e)}")
        return

    try:
        await transfer.wait_for_client_connected()
    except TimeoutError:
        log.warning("△ Receiver did not connect in time for resume.")
        await websocket.send_text("Error: Receiver did not connect in time.")
        return

    transfer.debug("△ Sending resume position...")
    await websocket.send_text(f"Resume from: {resume_from}")

    transfer.info("△ Resuming upload...")
    await transfer.collect_upload(
        stream=websocket.iter_bytes(),
        on_error=send_error_and_close(websocket),
        resume_from=resume_from
    )

    transfer.info("△ Resume upload complete.")
