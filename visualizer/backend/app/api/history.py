"""历史查询云存储 — 按用户隔离（多用户隔离 v1，2026-08-25）

替代原 localStorage 方案：换设备/浏览器不丢，团队内账号各自独立。
存储：data/history/{username}.json，每用户最多 100 条。
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/history", tags=["history"])

_HISTORY_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "history"
)
_MAX_ENTRIES = 100


def _user_file(username: str) -> Path:
    safe = "".join(c for c in username if c.isalnum() or c in ("_", "-")) or "unknown"
    return Path(_HISTORY_DIR) / f"{safe}.json"


def _load(username: str) -> List[dict]:
    try:
        p = _user_file(username)
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"history load failed for {username}: {e}")
    return []


def _save(username: str, entries: List[dict]):
    p = _user_file(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries[:_MAX_ENTRIES], f, ensure_ascii=False)
    os.replace(tmp, p)


@router.get("")
async def list_history(request: Request):
    """当前用户的历史查询列表（新→旧）。"""
    user = getattr(request.state, "user", "unknown")
    return {"entries": _load(user)}


@router.post("")
async def add_history(request: Request):
    """追加一条历史（相同 SQL 去重置顶）。"""
    user = getattr(request.state, "user", "unknown")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})
    sql = (body.get("sql") or "").strip()
    if not sql:
        return JSONResponse(status_code=400, content={"detail": "sql 不能为空"})
    entry = {
        "ts": int(time.time() * 1000),
        "question": body.get("question", ""),
        "sql": sql,
        "queryMode": body.get("queryMode", "sqlite"),
        "batchId": body.get("batchId", ""),
        "rowCount": body.get("rowCount", 0),
    }
    entries = _load(user)
    entries = [e for e in entries if e.get("sql") != sql]
    entries.insert(0, entry)
    _save(user, entries)
    return {"ok": True, "total": min(len(entries), _MAX_ENTRIES)}


@router.delete("")
async def clear_history(request: Request):
    """清空当前用户历史。"""
    user = getattr(request.state, "user", "unknown")
    _save(user, [])
    return {"ok": True}
