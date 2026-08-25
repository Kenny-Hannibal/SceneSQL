"""User Strategy API — 用户自定义SQL策略的CRUD接口

用户策略存储为 YAML（与系统 recipe 格式相同），存放于 user_strategies/ 目录。
策略变更后通知 ConceptRouter 重新加载映射。
认证由 auth_middleware 统一处理，路由无需额外 Depends。
"""
import logging
from fastapi import APIRouter, HTTPException, Request
from typing import List

from app.core.user_strategy import (
    UserStrategyManager,
    StrategyCreateRequest,
    StrategyUpdateRequest,
    StrategyInfo,
)
from app.core.eval_case_store import eval_case_store
from app.services.datamining import sync_strategy_to_dm, sync_strategy_reviews

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategies", tags=["strategies"])

# 策略存储目录：与 agent 的 user_strategies/ 共享，assembler 也从此加载
# strategies.py 位于 visualizer/backend/app/api/，需向上4级到项目根
import os
_STRATEGY_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..",
    "agent", "backend", "app", "core", "user_strategies"
))
_manager = UserStrategyManager(strategy_dir=_STRATEGY_DIR)

def _current_user(request: Request) -> str:
    return getattr(request.state, "user", "admin")


def _can_edit(info, user: str, env_admin: str) -> bool:
    """编辑权限：owner 本人或 admin。存量策略 owner=admin → 仅 admin 可改。"""
    return user == env_admin or getattr(info, "owner", "admin") == user


import os as _os
_ENV_ADMIN = _os.getenv("AUTH_USERNAME", "gac")



@router.get("", response_model=List[StrategyInfo])
async def list_strategies(request: Request):
    """列出策略（多用户 v1）：自己的 + admin 的（共享知识）。admin 看到全部。"""
    user = _current_user(request)
    all_strategies = _manager.list_strategies()
    if user == _ENV_ADMIN:
        return all_strategies
    return [s for s in all_strategies if s.owner in (user, _ENV_ADMIN)]


@router.get("/{name}", response_model=StrategyInfo)
async def get_strategy(name: str):
    """获取单个策略。"""
    try:
        return _manager.get_strategy(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")


@router.post("", response_model=StrategyInfo)
async def create_strategy(req: StrategyCreateRequest, request: Request):
    """创建新策略（owner=当前用户）。"""
    try:
        info = _manager.create_strategy(req, owner=_current_user(request))
        _reload_concept_router()
        return info
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/{name}", response_model=StrategyInfo)
async def update_strategy(name: str, req: StrategyUpdateRequest, request: Request):
    """更新策略（仅 owner 或 admin）。"""
    user = _current_user(request)
    try:
        existing = _manager.get_strategy(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")
    if not _can_edit(existing, user, _ENV_ADMIN):
        raise HTTPException(status_code=403, detail=f"只有创建者或 admin 可以修改策略 {name}")
    try:
        info = _manager.update_strategy(name, req)
        _reload_concept_router()
        return info
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")


@router.post("/{name}/sync-dm")
async def sync_strategy_dm(name: str):
    """把策略同步到 DataMining 平台 sql_strategy 表（新建或更新），
    并把该策略的标注 case 推送为策略评测记录（评测详情：通过/不通过）。"""
    try:
        info = _manager.get_strategy(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")
    try:
        result = await sync_strategy_to_dm(
            name=info.name,
            tag_name=getattr(info, "tag_name", None),
            sql=info.sql,
            description=getattr(info, "description", None),
        )
        # 推送评测详情（通过/不通过 case）
        reviews = {"pushed": 0, "skipped": 0, "failed": 0}
        sid = result.get("id")
        cases = eval_case_store.list_cases(name)
        if sid and cases:
            reviews = await sync_strategy_reviews(sid, cases)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("sync-dm failed for %s", name)
        raise HTTPException(status_code=502, detail=f"同步异常: {e}")
    return {"ok": True, **result, "reviews": reviews}


@router.delete("/{name}")
async def delete_strategy(name: str):
    """删除策略。"""
    try:
        _manager.delete_strategy(name)
        eval_case_store.clear(name)
        _reload_concept_router()
        return {"ok": True}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")


def _reload_concept_router():
    """策略变更后通知 ConceptRouter 重新加载用户策略映射。

    运行时动态导入 agent 的 concept_router 模块，避免 visualizer
    对 agent 产生编译期依赖。如果 agent 未加载（如独立部署），静默跳过。
    """
    try:
        import importlib
        mod = importlib.import_module("app.core.concept_router")
        if hasattr(mod, "get_concept_router"):
            cr = mod.get_concept_router()
            cr.load_user_strategies()
            logger.info("ConceptRouter reloaded with updated user strategies")
    except ImportError:
        logger.debug("agent concept_router not available, skip reload")
    except Exception as e:
        logger.warning(f"Failed to reload ConceptRouter: {e}")
