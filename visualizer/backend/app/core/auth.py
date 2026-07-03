"""JWT 认证模块 — 基于 python-jose + passlib 的轻量登录方案。

用户名/密码从环境变量 AUTH_USERNAME / AUTH_PASSWORD 读取（.env），
默认值 gac / gac_data。
"""

import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── 配置 ──
_AUTH_USERNAME = os.getenv("AUTH_USERNAME", "gac")
_AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "gac_data")
_JWT_SECRET = os.getenv("JWT_SECRET", "sceneSQL_visualizer_secret_key_2026")
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

# 延迟导入 jose —— 仅在登录/验证时使用
_jose_jwt = None


def _get_jose():
    global _jose_jwt
    if _jose_jwt is None:
        try:
            from jose import jwt as _jwt
            _jose_jwt = _jwt
        except ImportError:
            raise RuntimeError("python-jose[cryptography] not installed. Run: pip install 'python-jose[cryptography]'")
    return _jose_jwt


# ── 不需要认证的路径 ──
_PUBLIC_PATHS = {
    "/health",
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/login",
}

# 静态资源前缀 —— 这些也不需要认证
_PUBLIC_PREFIXES = ("/static/", "/assets/")


def is_public_path(path: str) -> bool:
    """判断请求路径是否不需要认证。"""
    if path in _PUBLIC_PATHS:
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def create_token(username: str) -> str:
    """生成 JWT token。"""
    jwt = _get_jose()
    expire = datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """验证 JWT token，返回 payload。失败则抛 HTTPException。"""
    jwt = _get_jose()
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def authenticate(username: str, password: str) -> str | None:
    """验证用户名密码，成功返回 JWT token，失败返回 None。"""
    if username == _AUTH_USERNAME and password == _AUTH_PASSWORD:
        logger.info("User '%s' logged in successfully", username)
        return create_token(username)
    logger.warning("Login failed for user '%s'", username)
    return None


async def auth_middleware(request: Request, call_next):
    """FastAPI 中间件：对非公开路径要求 Bearer token 认证。

    - 公开路径（/health, /, /api/auth/login, 静态资源）直接放行
    - 其他路径要求 Authorization: Bearer <token>
    - token 无效或过期返回 401
    """
    path = request.url.path

    if is_public_path(path):
        return await call_next(request)

    # 检查 Authorization header 或 ?token= 查询参数（后者用于 MSE 流式播放场景）
    auth_header = request.headers.get("Authorization", "")
    token = None

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # 尝试从查询参数获取 token（MSE fetch 无法设自定义 header）
        token_param = request.query_params.get("token")
        if token_param:
            token = token_param

    if not token:
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = verify_token(token)
        # 将用户信息注入 request state，供下游使用
        request.state.user = payload.get("sub", "unknown")
    except HTTPException:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)
