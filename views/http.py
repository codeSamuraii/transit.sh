import pathlib
import asyncio
from typing import Optional
from fastapi import Request, APIRouter
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, PlainTextResponse

from lib.logging import get_logger
from lib.transfer import FileMetadata, FileTransfer

router = APIRouter()
log = get_logger('http')


@router.put("/{uid}/{filename:path}")
async def http_upload(request: Request, uid: str, filename: str):
    """
    Upload a file via HTTP PUT.

    The filename can be provided as a path parameter: `/{uid}/{filename}` (automatically used by `curl --upload-file`)
    File size is limited to 100 MiB for HTTP transfers.
    """
    log.info(f"△ HTTP upload request: {filename}" )
    file = FileMetadata.get_from_http_headers(request.headers, filename)

    if file.size > 100*1024*1024:
        raise HTTPException(status_code=413, detail="File too large. 100MiB maximum for HTTP.")

    transfer = await FileTransfer.create(uid, file)

    try:
        await transfer.wait_for_client_connected()
    except asyncio.TimeoutError:
        log.warning("△ Client did not connect in time.")
        raise HTTPException(status_code=408, detail="Client did not connect in time.")

    transfer.info("△ Uploading...")
    await transfer.collect_upload(request.stream(), protocol='http')

    transfer.info("△ Upload complete.")
    return PlainTextResponse("Transfer complete.", status_code=200)


# Link prefetch protection
PREFETCHER_USER_AGENTS = {
    'whatsapp', 'facebookexternalhit', 'twitterbot', 'slackbot-linkexpanding',
    'discordbot', 'googlebot', 'bingbot', 'linkedinbot', 'pinterestbot', 'telegrambot',
}
def get_preview_html(**kwargs):
    try:
        template_path = pathlib.Path(__file__).parent.parent / 'static' / 'preview.html'
        with open(template_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        return PlainTextResponse("Preview template not found.", status_code=500)

    return html_content.format(**kwargs)


@router.get("/{uid}")
@router.get("/{uid}/")
async def http_download(request: Request, uid: str):
    """
    Download a file via HTTP GET.

    The uid is used to identify the file to download.
    File chunks are forwarded from sender to receiver via streaming.
    """
    log.info(f"▼ HTTP download for: {uid}")
    if '.' in uid:
        raise HTTPException(status_code=400, detail="Invalid transfer ID. Must not contain '.' or '/'.")

    try:
        transfer = await FileTransfer.get(uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transfer not found.")

    file_name, file_size, file_type = transfer.get_file_info()
    user_agent = request.headers.get('user-agent', '').lower()
    is_prefetcher = any(prefetch_ua in user_agent for prefetch_ua in PREFETCHER_USER_AGENTS)

    if is_prefetcher:
        transfer.info(f"▼ Prefetch request detected from User-Agent: {request.headers.get('user-agent')}. Serving metadata.")
        html_preview = get_preview_html(file_name=file_name, file_size=file_size, file_type=file_type)
        return HTMLResponse(content=html_preview, status_code=200)

    await transfer.set_client_connected()

    transfer.info(f"▼ Starting download of {file_name} ({file_size} bytes, type: {file_type})")
    data_stream = StreamingResponse(
        transfer.supply_download(),
        status_code=200,
        media_type=file_type,
        headers={"Content-Disposition": f"attachment; filename={file_name}", "Content-Length": str(file_size)}
    )

    return data_stream
