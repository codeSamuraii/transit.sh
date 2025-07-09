from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

@router.get("/health")
async def get_health():
    return {"status": "ok"}

@router.get("/robots.txt")
async def get_robots_txt():
    return FileResponse("static/robots.txt", content_disposition_type="inline")
