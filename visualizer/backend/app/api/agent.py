import os
import logging
import json
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.models.schemas import AgentQueryRequest, AgentQueryResponse, ExecuteSQLRequest
from app.core.config import settings

# Ensure agent path is available for import
import sys
AGENT_DIR = str(settings.PROJECT_ROOT)
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from agent.backend.app.services.agent_engine import AgentEngine, FALLBACK_SYSTEM_PROMPT as SYSTEM_PROMPT

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)

# Lazy-init engine per (db_path, query_mode, batch_id)
_engines: dict = {}


def _resolve_db_path(req: AgentQueryRequest):
    """根据请求参数解析最终 db_path 和 query_mode。

    优先级：
    1. 手动填写的 db_path（向后兼容）
    2. batch_id + query_mode（新逻辑）
    3. .env 默认值
    """
    # P1: 手动路径
    if req.db_path and req.db_path.strip():
        return req.db_path.strip(), req.query_mode or settings.QUERY_MODE or "sqlite"

    # P2: batch_id + query_mode
    query_mode = (req.query_mode or settings.QUERY_MODE or "sqlite").lower()
    batch_id = req.batch_id

    if query_mode == "sqlite":
        base_path = Path(settings.SQLITE_DB_PATH) if settings.SQLITE_DB_PATH else Path("/mnt/gacrnd-oss/gac_liulian/common_data")
        if batch_id:
            db_path = str(base_path / "sqlite_dbs" / batch_id)
        else:
            db_path = str(base_path)
    else:  # parquet
        base_path = Path(settings.ETL_BASE_PATH) if settings.ETL_BASE_PATH else (
            Path(settings.SQLITE_DB_PATH) / "parquet" if settings.SQLITE_DB_PATH else Path("/mnt/gacrnd-oss/gac_liulian/common_data/parquet")
        )
        if batch_id:
            db_path = str(base_path / batch_id)
        else:
            db_path = str(base_path)

    return db_path, query_mode


def _get_engine(db_path: str, query_mode: str = "", batch_id: str = "") -> AgentEngine:
    """获取或创建 AgentEngine，支持动态切换 Parquet batch。"""
    cache_key = f"{db_path}:{query_mode}:{batch_id}"
    logger.info("_get_engine cache_key=%s in_cache=%s", cache_key, cache_key in _engines)
    if cache_key not in _engines:
        # 确保 ETL 相关环境变量已注入（pydantic_settings 不会自动写入 os.environ）
        if settings.ETL_BASE_PATH:
            os.environ["ETL_BASE_PATH"] = settings.ETL_BASE_PATH
            logger.info("Injected ETL_BASE_PATH=%s", settings.ETL_BASE_PATH)
        if settings.ETL_BATCH_ID:
            os.environ["ETL_BATCH_ID"] = settings.ETL_BATCH_ID
        if query_mode == "parquet" and batch_id:
            # 临时切换 Parquet batch：更新 ETL_BATCH_ID 并清除单例缓存
            os.environ["ETL_BATCH_ID"] = batch_id
            import agent.backend.app.services.etl.etl_manifest as em_module
            em_module._default_manager = None
            logger.info("Cleared etl_manifest singleton, ETL_BATCH_ID=%s", batch_id)
        _engines[cache_key] = AgentEngine(db_path, query_mode=query_mode)
    return _engines[cache_key]


@router.get("/batches")
def list_batches():
    """列出 SQLITE_DB_PATH 下所有可用的 batch。"""
    batches = []
    base_path = Path(settings.SQLITE_DB_PATH) if settings.SQLITE_DB_PATH else Path("/mnt/gacrnd-oss/gac_liulian/common_data")
    sqlite_dbs_dir = base_path / "sqlite_dbs"
    parquet_dir = base_path / "parquet"

    if sqlite_dbs_dir.exists():
        for batch_dir in sorted(sqlite_dbs_dir.iterdir()):
            if batch_dir.is_dir():
                db_count = len(list(batch_dir.glob("*.db")))
                has_parquet = (parquet_dir / batch_dir.name / "manifest.yaml").exists()
                batches.append({
                    "batch_id": batch_dir.name,
                    "sqlite_count": db_count,
                    "has_parquet": has_parquet,
                })

    return batches


@router.get("/resolve-bag-path")
async def resolve_bag_path(bag_id: str):
    """根据 bag_id 解析 bag 本地路径（用于前端可视化）。"""
    try:
        from tools.rosbag_path_resolver import RosbagPathResolver
        resolver = RosbagPathResolver()
        info = resolver.resolve(bag_id)
        return {
            "bag_id": bag_id,
            "origin_bag_id": info.origin_bag_id,
            "bag_path": info.local_path or info.oss_path or "",
            "oss_path": info.oss_path or "",
            "local_path": info.local_path or "",
        }
    except ImportError:
        return {"bag_id": bag_id, "bag_path": "", "error": "dm_sdk not installed"}
    except Exception as e:
        return {"bag_id": bag_id, "bag_path": "", "error": str(e)}


@router.post("/query", response_model=AgentQueryResponse)
async def agent_query(req: AgentQueryRequest):
    db_path, query_mode = _resolve_db_path(req)

    if not db_path:
        return AgentQueryResponse(
            sql="",
            explanation="",
            columns=[],
            rows=[],
            error="SQLITE_DB_PATH not configured",
        )

    # Handle oss:// paths
    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            return AgentQueryResponse(
                sql="",
                explanation="",
                columns=[],
                rows=[],
                error=str(e),
            )

    # SQLite 模式下需要验证路径存在；Parquet 模式下路径可能不存在（直接读 manifest）
    if query_mode == "sqlite" and not os.path.exists(db_path):
        return AgentQueryResponse(
            sql="",
            explanation="",
            columns=[],
            rows=[],
            error=f"DB path not found: {db_path}",
        )

    logger.info("Agent query: %s | mode: %s | db: %s | limit: %s", req.question, query_mode, db_path, req.result_limit)
    engine = _get_engine(db_path, query_mode=query_mode, batch_id=req.batch_id)
    result = await engine.query(req.question, result_limit=req.result_limit, db_limit=req.db_limit)

    return AgentQueryResponse(
        sql=result.sql,
        explanation=result.explanation,
        columns=result.columns,
        rows=result.rows,
        error=result.error,
        scanned_dbs=result.scanned_dbs,
        matched_dbs=result.matched_dbs,
    )


@router.post("/query-stream")
async def agent_query_stream(req: AgentQueryRequest):
    """SSE streaming endpoint for agent query progress."""
    db_path, query_mode = _resolve_db_path(req)

    if not db_path:
        async def empty_err():
            yield f"data: {json.dumps({'stage': 'error', 'message': 'SQLITE_DB_PATH not configured'})}\n\n"
        return StreamingResponse(empty_err(), media_type="text/event-stream")

    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            async def oss_err():
                yield f"data: {json.dumps({'stage': 'error', 'message': str(e)})}\n\n"
            return StreamingResponse(oss_err(), media_type="text/event-stream")

    if query_mode == "sqlite" and not os.path.exists(db_path):
        async def not_found_err():
            yield f"data: {json.dumps({'stage': 'error', 'message': f'DB path not found: {db_path}'})}\n\n"
        return StreamingResponse(not_found_err(), media_type="text/event-stream")

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=req.batch_id)

    async def event_generator():
        yield f"data: {json.dumps({'stage': 'understanding', 'message': '正在理解您的问题...'})}\n\n"

        prompt = f"""
{engine.schema_text}

用户问题：{req.question}

请生成 SQLite SQL（只输出纯 SQL，不要解释，不要带 LIMIT）：
"""
        yield f"data: {json.dumps({'stage': 'generating', 'message': '正在生成 SQL...'})}\n\n"

        try:
            raw_sql = await engine.llm.chat(SYSTEM_PROMPT, prompt, temperature=0.1)
        except Exception as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': f'LLM 调用失败: {e}'})}\n\n"
            return

        sql = engine._clean_sql(raw_sql)
        yield f"data: {json.dumps({'stage': 'sql_generated', 'sql': sql})}\n\n"

        validation_error = engine._validate_sql(sql)
        if validation_error:
            yield f"data: {json.dumps({'stage': 'validation_failed', 'message': validation_error, 'sql': sql})}\n\n"
            return

        yield f"data: {json.dumps({'stage': 'executing', 'message': 'SQL 校验通过，正在执行查询...'})}\n\n"

        try:
            if engine.query_mode == "parquet":
                result = await engine._query_parquet(sql, result_limit=req.result_limit)
            elif engine.is_dir:
                result = await engine._query_batch(sql, result_limit=req.result_limit, db_limit=req.db_limit)
            else:
                result = await engine._query_single(sql, result_limit=req.result_limit)
        except Exception as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': f'执行失败: {e}'})}\n\n"
            return

        yield f"data: {json.dumps({'stage': 'completed', 'sql': result.sql, 'explanation': result.explanation, 'columns': result.columns, 'rows': result.rows, 'error': result.error, 'scanned_dbs': result.scanned_dbs, 'matched_dbs': result.matched_dbs})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/execute-sql", response_model=AgentQueryResponse)
async def execute_sql(req: ExecuteSQLRequest):
    """直接执行用户提供的 SQL（不经 LLM）。"""
    db_path, query_mode = _resolve_db_path(AgentQueryRequest(
        question="",
        db_path=req.db_path,
        batch_id=req.batch_id,
        query_mode=req.query_mode,
        db_limit=req.db_limit,
        result_limit=req.result_limit,
    ))

    if not db_path:
        return AgentQueryResponse(
            sql=req.sql, explanation="", columns=[], rows=[],
            error="SQLITE_DB_PATH not configured",
        )

    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=str(e))

    if query_mode == "sqlite" and not os.path.exists(db_path):
        return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=f"DB path not found: {db_path}")

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=req.batch_id)

    # SQL 校验
    validation_error = engine._validate_sql(req.sql)
    if validation_error:
        return AgentQueryResponse(sql=req.sql, explanation="SQL 校验失败", columns=[], rows=[], error=validation_error)

    try:
        if query_mode == "parquet":
            result = await engine._query_parquet(req.sql, result_limit=req.result_limit)
        elif engine.is_dir:
            result = await engine._query_batch(req.sql, result_limit=req.result_limit, db_limit=req.db_limit)
        else:
            result = await engine._query_single(req.sql, result_limit=req.result_limit)
    except Exception as e:
        return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=str(e))

    return AgentQueryResponse(
        sql=result.sql,
        explanation=result.explanation,
        columns=result.columns,
        rows=result.rows,
        error=result.error,
        scanned_dbs=result.scanned_dbs,
        matched_dbs=result.matched_dbs,
    )


@router.post("/generate-sql")
async def generate_sql_only(req: AgentQueryRequest):
    """仅生成 SQL，不执行。返回生成的 SQL 供用户审查/修改。"""
    db_path, query_mode = _resolve_db_path(req)

    if not db_path:
        return {"sql": "", "error": "SQLITE_DB_PATH not configured"}

    if query_mode == "sqlite" and not os.path.exists(db_path):
        return {"sql": "", "error": f"DB path not found: {db_path}"}

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=req.batch_id)

    # 路由 + 分层注入（与 query 相同逻辑）
    route = engine.router.route(req.question)
    from agent.backend.app.core.schema_reader import format_schema_for_prompt
    from agent.backend.app.core.tag_router import build_prompt

    schema_text = format_schema_for_prompt(
        engine.schema,
        only_tables=route.involved_tables if route.involved_tables else None,
    )
    system_prompt, user_prompt = build_prompt(
        question=req.question,
        schema_text=schema_text,
        route=route,
    )

    try:
        raw_sql = await engine.llm.chat(system_prompt, user_prompt, temperature=0.1)
        sql = engine._clean_sql(raw_sql)
        validation_error = engine._validate_sql(sql)
        return {
            "sql": sql,
            "validation_error": validation_error,
            "route_method": route.method,
            "matched_tags": [t.tag_name for t in route.matched_tags],
            "involved_tables": sorted(route.involved_tables),
        }
    except Exception as e:
        return {"sql": "", "error": str(e)}
