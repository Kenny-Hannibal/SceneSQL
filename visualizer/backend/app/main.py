import os
import sys
from pathlib import Path

# Ensure backend package imports work when launched from project root
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from app.api import bag, video, agent
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.exceptions import setup_exception_handlers

setup_logging()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Rosbag multi-camera visualizer backend API",
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url="/redoc" if settings.ENV != "production" else None,
)

setup_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bag.router)
app.include_router(video.router)
app.include_router(agent.router)

# Serve frontend build if available
_FRONTEND_BUILD = settings.PROJECT_ROOT / "visualizer" / "frontend" / "build"
_INDEX_HTML = _FRONTEND_BUILD / "index.html"

if _FRONTEND_BUILD.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_BUILD / "static"), check_dir=False), name="static")
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_BUILD / "assets"), check_dir=False), name="assets")


@app.get("/", tags=["ui"])
def root(request: Request):
    if _INDEX_HTML.exists():
        return HTMLResponse(content=_INDEX_HTML.read_text(encoding="utf-8"))
    return {"message": f"{settings.PROJECT_NAME} is running", "version": settings.VERSION}


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
