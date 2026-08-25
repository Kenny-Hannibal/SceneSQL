"""用户存储 — 多用户隔离 v1（2026-08-25）

JSON 文件存储（data/users.json），pbkdf2 哈希（stdlib，无新依赖）。
角色：env 账户（AUTH_USERNAME）恒为 admin；注册用户默认为 user。
"""
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

USERS_FILE = os.environ.get(
    "SCENESQL_USERS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "users.json"),
)
_PBKDF2_ROUNDS = 100_000


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS).hex()


def _load() -> dict:
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"users.json load failed (ignored): {e}")
    return {}


def _save(users: dict):
    path = Path(USERS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def user_exists(username: str) -> bool:
    return username in _load()


def create_user(username: str, password: str, role: str = "user") -> None:
    """注册新用户。已存在则抛 ValueError。"""
    username = username.strip()
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    users = _load()
    if username in users:
        raise ValueError(f"用户已存在: {username}")
    salt = secrets.token_hex(16)
    users[username] = {
        "password_hash": _hash_password(password, salt),
        "salt": salt,
        "role": role,
        "created_at": time.time(),
    }
    _save(users)
    logger.info(f"User registered: {username} (role={role})")


def verify_user(username: str, password: str) -> Optional[dict]:
    """校验注册用户的用户名密码，成功返回 {username, role}，失败 None。"""
    u = _load().get(username)
    if not u:
        return None
    if _hash_password(password, u["salt"]) == u["password_hash"]:
        return {"username": username, "role": u.get("role", "user")}
    return None


def get_role(username: str, env_admin: str) -> str:
    """获取用户角色：env 账户恒为 admin，其余查 store。"""
    if username == env_admin:
        return "admin"
    u = _load().get(username)
    return u.get("role", "user") if u else "user"
