import string
from pydantic import ValidationError
from fastapi import WebSocket, APIRouter

from lib.logging import get_logger
from lib.transfer import FileTransfer
from lib.metadata import FileMetadata
from lib.models import ClientState

router = APIRouter()
log = get_logger('websockets')


@router.websocket("/send/{uid}")
async def websocket_upload(websocket: WebSocket, uid: str):
    """Handles WebSocket file uploads."""
    if any(char not in string.ascii_letters + string.digits + '-' for char in uid):
        log.debug("△ Invalid transfer ID")
        await websocket.close(code=1008, reason="Invalid transfer ID")
        return

    await websocket.accept()
    log.debug("△ Websocket upload request")

    try:
        header = await websocket.receive_json()
        file = FileMetadata.get_from_json(header)
    except ValidationError as e:
        log.warning("△ Invalid file metadata JSON header", exc_info=e)
        await websocket.send_text("Error: Invalid file metadata JSON header")
        return
    except Exception as e:
        log.error("△ Cannot decode file metadata JSON header", exc_info=e)
        await websocket.send_text("Error: Cannot decode file metadata JSON header")
        return

    log.info(f"△ Creating transfer: {file}")

    try:
        transfer = await FileTransfer.create(uid, file)
    except KeyError:
        log.warning("△ Transfer ID is already used")
        await websocket.send_text("Error: Transfer ID is already used")
        return
    except (TypeError, ValidationError) as e:
        log.error("△ Invalid transfer ID or file metadata", exc_info=e)
        await websocket.send_text("Error: Invalid transfer ID or file metadata")
        return

    try:
        await transfer.wait_for_client_connected()
    except TimeoutError:
        log.warning("△ Receiver did not connect in time")
        await websocket.send_text("Error: Receiver did not connect in time")
        return

    transfer.debug("△ Sending go-ahead...")
    await websocket.send_text("Go for file chunks")

    transfer.info("△ Starting upload...")
    await transfer.collect_upload(stream=websocket.iter_bytes())

    sender_state = await transfer.store.get_sender_state()
    if sender_state == ClientState.COMPLETE:
        transfer.info("△ Upload complete")
    elif sender_state == ClientState.ERROR:
        await websocket.send_text("Error: Transfer failed")


@router.websocket("/resume/{uid}")
async def websocket_resume_upload(websocket: WebSocket, uid: str):
    """Resume an interrupted WebSocket upload."""
    if any(char not in string.ascii_letters + string.digits + '-' for char in uid):
        log.debug("△ Invalid transfer ID")
        await websocket.close(code=1008, reason="Invalid transfer ID")
        return

    await websocket.accept()
    log.debug(f"△ Resume upload request for {uid}")

    try:
        header = await websocket.receive_json()
        file = FileMetadata.get_from_json(header)
    except ValidationError as e:
        log.warning("△ Invalid file metadata JSON header", exc_info=e)
        await websocket.send_text("Error: Invalid file metadata JSON header")
        return
    except Exception as e:
        log.error("△ Cannot decode file metadata JSON header", exc_info=e)
        await websocket.send_text("Error: Cannot decode file metadata JSON header")
        return

    try:
        transfer = await FileTransfer.get(uid)

        stored_file = transfer.file
        if stored_file.name != file.name or stored_file.size != file.size or stored_file.type != file.type:
            log.warning("△ Resume request does not match original transfer")
            await websocket.send_text("Error: File metadata does not match original transfer")
            return

        resume_from = await transfer.get_resume_position()
        log.info(f"△ Resuming transfer from byte {resume_from}: {file}")

    except KeyError:
        log.warning("△ Transfer not found for resumption")
        await websocket.send_text("Error: Transfer not found")
        return
    except Exception as e:
        log.error("△ Error preparing resume", exc_info=e)
        await websocket.send_text(f"Error: {str(e)}")
        return

    transfer.debug("△ Sending resume position...")
    await websocket.send_text(f"Resume from: {resume_from}")

    transfer.info("△ Resuming upload...")
    await transfer.collect_upload(stream=websocket.iter_bytes(), resume_from=resume_from)

    sender_state = await transfer.store.get_sender_state()
    if sender_state == ClientState.COMPLETE:
        transfer.info("△ Resume upload complete")
    elif sender_state == ClientState.ERROR:
        await websocket.send_text("Error: Transfer failed")