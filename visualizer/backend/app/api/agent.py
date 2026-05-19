import os
import logging
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.models.schemas import AgentQueryRequest, AgentQueryResponse
from app.core.config import settings

# Ensure agent path is available for import
import sys
AGENT_DIR = str(settings.PROJECT_ROOT / "agent" / "backend")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from agent.backend.app.services.agent_engine import AgentEngine, SYSTEM_PROMPT

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)

# Lazy-init engine per db_path
_engines: dict = {}


def _get_engine(db_path: str) -> AgentEngine:
    if db_path not in _engines:
        _engines[db_path] = AgentEngine(db_path)
    return _engines[db_path]


@router.post("/query", response_model=AgentQueryResponse)
async def agent_query(req: AgentQueryRequest):
    db_path = req.db_path or settings.SQLITE_DB_PATH
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

    if not os.path.exists(db_path):
        return AgentQueryResponse(
            sql="",
            explanation="",
            columns=[],
            rows=[],
            error=f"DB path not found: {db_path}",
        )

    logger.info("Agent query: %s | db: %s | limit: %s", req.question, db_path, req.result_limit)
    engine = _get_engine(db_path)
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
    db_path = req.db_path or settings.SQLITE_DB_PATH
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

    if not os.path.exists(db_path):
        async def not_found_err():
            yield f"data: {json.dumps({'stage': 'error', 'message': f'DB path not found: {db_path}'})}\n\n"
        return StreamingResponse(not_found_err(), media_type="text/event-stream")

    engine = _get_engine(db_path)

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
            if engine.is_dir:
                result = await engine._query_batch(sql, result_limit=req.result_limit, db_limit=req.db_limit)
            else:
                result = await engine._query_single(sql, result_limit=req.result_limit)
        except Exception as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': f'执行失败: {e}'})}\n\n"
            return

        yield f"data: {json.dumps({'stage': 'completed', 'sql': result.sql, 'explanation': result.explanation, 'columns': result.columns, 'rows': result.rows, 'error': result.error, 'scanned_dbs': result.scanned_dbs, 'matched_dbs': result.matched_dbs})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
