import string
import asyncio
from fastapi import Request, APIRouter
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import ValidationError

from lib.logging import get_logger
from lib.callbacks import raise_http_exception
from lib.transfer import FileTransfer
from lib.metadata import FileMetadata

router = APIRouter()
log = get_logger('http')
templates = Jinja2Templates(directory="static/templates")


@router.put("/{uid}/{filename}")
async def http_upload(request: Request, uid: str, filename: str):
    """
    Upload a file via HTTP PUT.

    The filename is provided as a path parameter after the transfer ID.
    When using cURL with the `-T`/`--upload-file` option, the filename is automatically added if the URL ends with a slash.
    File size is limited to 1GiB for HTTP transfers.
    """
    if any(char not in string.ascii_letters + string.digits + '-' for char in uid):
        raise HTTPException(status_code=400, detail="Invalid transfer ID. Must only contain alphanumeric characters and hyphens.")
    log.debug("△ HTTP upload request.")

    try:
        file = FileMetadata.get_from_http_headers(request.headers, filename)
    except KeyError as e:
        log.error("△ Cannot decode file metadata from HTTP headers.", exc_info=e)
        raise HTTPException(status_code=400, detail="Cannot decode file metadata from HTTP headers.")
    except ValidationError as e:
        log.error("△ Invalid file metadata.", exc_info=e)
        raise HTTPException(status_code=400, detail="Invalid file metadata.")

    if file.size > 1024**3:
        raise HTTPException(status_code=413, detail="File too large. 1GiB maximum for HTTP.")

    log.info(f"△ Creating transfer: {file}")

    try:
        transfer = await FileTransfer.create(uid, file)
    except KeyError as e:
        log.warning("△ Transfer ID is already used.")
        raise HTTPException(status_code=409, detail="Transfer ID is already used.")
    except (TypeError, ValidationError) as e:
        log.error("△ Invalid transfer ID or file metadata.", exc_info=e)
        raise HTTPException(status_code=400, detail="Invalid transfer ID or file metadata.")

    try:
        await transfer.wait_for_client_connected()
    except asyncio.TimeoutError:
        log.warning("△ Receiver did not connect in time.")
        raise HTTPException(status_code=408, detail="Client did not connect in time.")

    transfer.info("△ Starting upload...")
    await transfer.collect_upload(
        stream=request.stream(),
        on_error=raise_http_exception(request),
    )

    transfer.info("△ Upload complete.")
    return PlainTextResponse("Transfer complete.", status_code=200)


# Link prefetch protection
PREFETCHER_USER_AGENTS = {
    'whatsapp', 'facebookexternalhit', 'twitterbot', 'slackbot-linkexpanding',
    'discordbot', 'googlebot', 'bingbot', 'linkedinbot', 'pinterestbot', 'telegrambot',
}

@router.get("/{uid}")
@router.get("/{uid}/")
async def http_download(request: Request, uid: str):
    """
    Download a file via HTTP GET.

    The uid is used to identify the file to download.
    File chunks are forwarded from sender to receiver via streaming.
    """
    if any(char not in string.ascii_letters + string.digits + '-' for char in uid):
        raise HTTPException(status_code=400, detail="Invalid transfer ID. Must only contain alphanumeric characters and hyphens.")

    try:
        transfer = await FileTransfer.get(uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transfer not found.")
    except (TypeError, ValidationError) as e:
        log.error("▼ Invalid transfer ID.", exc_info=e)
        raise HTTPException(status_code=400, detail="Invalid transfer ID.")
    else:
        log.info(f"▼ HTTP download request for: {transfer.file}")

    file_name, file_size, file_type = transfer.get_file_info()
    user_agent = request.headers.get('user-agent', '').lower()
    is_prefetcher = any(prefetch_ua in user_agent for prefetch_ua in PREFETCHER_USER_AGENTS)
    is_curl = 'curl' in user_agent

    if is_prefetcher:
        log.info(f"▼ Prefetch request detected, serving preview. UA: ({request.headers.get('user-agent')})")
        return templates.TemplateResponse(request, "preview.html", transfer.file.to_readable_dict())

    if not is_curl and not request.query_params.get('download'):
        log.info(f"▼ Browser request detected, serving download page. UA: ({request.headers.get('user-agent')})")
        return templates.TemplateResponse(request, "download.html", transfer.file.to_readable_dict() | {'receiver_connected': await transfer.is_receiver_connected()})

    elif not await transfer.set_receiver_connected():
        raise HTTPException(status_code=409, detail="A client is already downloading this file.")

    await transfer.set_client_connected()

    transfer.info("▼ Starting download...")
    data_stream = StreamingResponse(
        transfer.supply_download(on_error=raise_http_exception(request)),
        status_code=200,
        media_type=file_type,
        background=BackgroundTask(transfer.finalize_download),
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
    )

    return data_stream
