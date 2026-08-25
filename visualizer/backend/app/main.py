import os
import sys
from pathlib import Path

# Ensure backend package imports work when launched from project root
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import logging
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from app.api import bag, video, agent, strategies, fusion_map, eval_labels, mage_vl, history
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.exceptions import setup_exception_handlers
from app.core.auth import auth_middleware, authenticate

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Rosbag multi-camera visualizer backend API",
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url="/redoc" if settings.ENV != "production" else None,
)

setup_exception_handlers(app)

# ── 认证中间件（必须在 CORS 之前，这样 401 响应也有正确的 CORS 头） ──
app.middleware("http")(auth_middleware)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有 HTTP 请求的耗时和状态码，便于定位卡死/慢请求。"""
    start = time.time()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as exc:
        status = 500
        logger.exception("Unhandled exception in request: %s %s", request.method, request.url.path)
        raise
    finally:
        duration = time.time() - start
        path = request.url.path
        if path != "/health":  # 跳过健康检查，避免日志刷屏
            if status >= 500 or duration > 2.0:
                logger.warning("%s %s | status=%s | duration=%.3fs", request.method, path, status, duration)
            else:
                logger.info("%s %s | status=%s | duration=%.3fs", request.method, path, status, duration)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 登录接口 ──
@app.post("/api/auth/login", tags=["auth"])
async def login(request: Request):
    """用户名密码登录，返回 JWT token。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    username = body.get("username", "")
    password = body.get("password", "")

    token = authenticate(username, password)
    if token is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    return {"access_token": token, "token_type": "bearer"}


# ── 验证 token 是否有效（前端启动时调用） ──
@app.get("/api/auth/verify", tags=["auth"])
async def verify_auth(request: Request):
    """验证当前 token 是否有效。auth_middleware 已验证通过，直接返回 200。"""
    user = getattr(request.state, "user", "unknown")
    return {"valid": True, "user": user}


# ── 注册接口（多用户隔离 v1，开放注册）──
@app.post("/api/auth/register", tags=["auth"])
async def register(request: Request):
    """注册新用户（role=user）。成功自动登录返回 token。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        return JSONResponse(status_code=400, content={"detail": "用户名和密码不能为空"})
    try:
        from app.core.users import create_user
        create_user(username, password)
    except ValueError as e:
        return JSONResponse(status_code=409, content={"detail": str(e)})
    token = authenticate(username, password)
    return {"access_token": token, "token_type": "bearer"}


app.include_router(bag.router)
app.include_router(video.router)
app.include_router(agent.router)
app.include_router(strategies.router)
app.include_router(history.router)
app.include_router(fusion_map.router)
app.include_router(eval_labels.router)
app.include_router(mage_vl.router)

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
