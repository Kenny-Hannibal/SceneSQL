import os
import logging
import json
import re
import io
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse, Response
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


def _detect_path_type(path: str) -> tuple[str, str]:
    """探测路径类型，返回 (detected_mode, batch_id_from_path)。

    规则：
    - 文件且以 .db 结尾  → ("sqlite", "")
    - 文件夹含 .db 文件   → ("sqlite", "")
    - 文件夹含 manifest.yaml 或 .parquet → ("parquet", 文件夹名)
    - 明显不是路径（含空格、中文标点等） → ("invalid", "")
    - 其他               → ("", "")
    """
    p = Path(path)

    # 明显不是路径的字符串（含空格、中文标点、中文字符且无路径分隔符）
    if any(c in path for c in [" ", "，", "。", "？", "！", "\n", "\t"]):
        return "invalid", ""
    if re.search(r'[\u4e00-\u9fff]', path) and "/" not in path and "\\" not in path and p.suffix != ".db":
        return "invalid", ""

    # 路径后缀强暗示类型（即使文件不存在）
    if p.suffix == ".db":
        return "sqlite", ""

    if not p.exists():
        return "", ""

    if p.is_file():
        return "", ""

    # 目录
    has_db = False
    try:
        has_db = any(p.glob("*.db"))
    except (OSError, PermissionError):
        pass

    has_manifest = (p / "manifest.yaml").exists()
    has_parquet = False
    try:
        has_parquet = any(p.glob("*.parquet"))
    except (OSError, PermissionError):
        pass

    if has_manifest or has_parquet:
        return "parquet", p.name
    if has_db:
        return "sqlite", ""
    return "", ""


def _resolve_db_path(req: AgentQueryRequest):
    """根据请求参数解析最终 db_path、query_mode 和 batch_id。

    返回值: (db_path, query_mode, resolved_batch_id)

    优先级：
    1. 手动填写的 db_path（向后兼容，自动探测类型）
    2. batch_id + query_mode（新逻辑）
    3. .env 默认值
    """
    # P1: 手动路径
    if req.db_path and req.db_path.strip():
        raw_path = req.db_path.strip()
        detected_mode, detected_batch_id = _detect_path_type(raw_path)

        if detected_mode == "invalid":
            # 用户把自然语言误填入了 db_path 框
            return raw_path, "invalid", ""

        # 用户显式指定的 query_mode 优先；否则自动探测；最后用默认值
        query_mode = (req.query_mode or detected_mode or settings.QUERY_MODE or "sqlite").lower()

        if query_mode == "parquet" and detected_batch_id:
            # 用户输入的是完整 parquet batch 路径，如 .../parquet/BATCH_ID
            # db_path 指向 base_dir（parquet 的父目录），batch_id 为文件夹名
            p = Path(raw_path)
            db_path = str(p.parent)
            return db_path, query_mode, detected_batch_id
        else:
            return raw_path, query_mode, ""

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
        # Parquet 模式使用 ETL_BASE_PATH（转换脚本的输出目录）
        base_path = Path(settings.ETL_BASE_PATH) if settings.ETL_BASE_PATH else (
            Path(settings.SQLITE_DB_PATH) / "parquet" if settings.SQLITE_DB_PATH else Path("/mnt/gacrnd-oss/gac_liulian/common_data/parquet")
        )
        # db_path 始终是 base_dir（parquet 的父目录），batch_id 单独传递
        db_path = str(base_path)

    return db_path, query_mode, batch_id or ""


def _validate_db_path(db_path: str, query_mode: str, batch_id: str = "") -> Optional[str]:
    """校验 db_path 是否有效，返回错误消息或 None。"""
    if query_mode == "invalid":
        return f"路径格式无效，疑似自然语言被误填入路径输入框: '{db_path}'。请清空路径框或输入有效的文件/文件夹路径。"

    if not db_path:
        return "SQLITE_DB_PATH not configured"

    if db_path.startswith("oss://"):
        return None  # 后续由 resolve_db_path 处理

    if query_mode == "sqlite":
        if not os.path.exists(db_path):
            return f"DB path not found: {db_path}"
        if os.path.isfile(db_path):
            if not db_path.endswith(".db"):
                return f"SQLite 文件必须以 .db 结尾: {db_path}"
        else:
            # 批量文件夹：检查是否有 .db 文件
            try:
                has_db = any(Path(db_path).glob("*.db"))
            except (OSError, PermissionError) as e:
                return f"无法读取目录 {db_path}: {e}"
            if not has_db:
                return f"目录 {db_path} 下没有找到 .db 文件"
    else:  # parquet
        # 检查 manifest.yaml 是否存在
        manifest_path = Path(db_path) / batch_id / "manifest.yaml" if batch_id else Path(db_path) / "manifest.yaml"
        if not manifest_path.exists():
            return f"Parquet manifest 未找到: {manifest_path}"

    return None


def _get_engine(db_path: str, query_mode: str = "", batch_id: str = "") -> AgentEngine:
    """获取或创建 AgentEngine，支持动态切换 Parquet batch。"""
    cache_key = f"{db_path}:{query_mode}:{batch_id}"
    logger.info("_get_engine cache_key=%s in_cache=%s", cache_key, cache_key in _engines)
    if cache_key not in _engines:
        # 确保 ETL 相关环境变量已注入（pydantic_settings 不会自动写入 os.environ）
        if query_mode == "parquet":
            # parquet 模式下 db_path 是 base_dir，必须注入到 ETL_BASE_PATH
            os.environ["ETL_BASE_PATH"] = db_path
            logger.info("Injected ETL_BASE_PATH=%s (from db_path)", db_path)
        elif settings.ETL_BASE_PATH:
            os.environ["ETL_BASE_PATH"] = settings.ETL_BASE_PATH
            logger.info("Injected ETL_BASE_PATH=%s (from settings)", settings.ETL_BASE_PATH)

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
    """列出所有可用的 batch（同时扫描 sqlite_dbs/ 和 parquet/ 目录）。"""
    sqlite_base = Path(settings.SQLITE_DB_PATH) if settings.SQLITE_DB_PATH else Path("/mnt/gacrnd-oss/gac_liulian/common_data")
    sqlite_dbs_dir = sqlite_base / "sqlite_dbs"
    # Parquet 目录使用 ETL_BASE_PATH（转换脚本的输出目录）
    parquet_base = Path(settings.ETL_BASE_PATH) if settings.ETL_BASE_PATH else (
        sqlite_base / "parquet" if settings.SQLITE_DB_PATH else Path("/mnt/gacrnd-oss/gac_liulian/common_data/parquet")
    )
    parquet_dir = parquet_base

    batch_map: dict[str, dict] = {}

    # 扫描 sqlite_dbs 目录
    try:
        if sqlite_dbs_dir.exists():
            for batch_dir in sorted(sqlite_dbs_dir.iterdir()):
                if batch_dir.is_dir():
                    db_count = len(list(batch_dir.glob("*.db")))
                    has_parquet = (parquet_dir / batch_dir.name / "manifest.yaml").exists()
                    batch_map[batch_dir.name] = {
                        "batch_id": batch_dir.name,
                        "sqlite_count": db_count,
                        "has_parquet": has_parquet,
                    }
    except OSError as e:
        logger.warning("sqlite_dbs 目录扫描失败（可能是 OSS FUSE 挂载断开）: %s", e)

    # 扫描 parquet 目录，补充只有 Parquet 没有 SQLite 的 batch
    try:
        if parquet_dir.exists():
            for batch_dir in sorted(parquet_dir.iterdir()):
                if batch_dir.is_dir() and (batch_dir / "manifest.yaml").exists():
                    bag_count = 0
                    try:
                        import yaml
                        with open(batch_dir / "manifest.yaml", "r", encoding="utf-8") as f:
                            manifest = yaml.safe_load(f)
                        bag_count = manifest.get("bag_count", 0)
                    except Exception:
                        pass
                    if batch_dir.name not in batch_map:
                        batch_map[batch_dir.name] = {
                            "batch_id": batch_dir.name,
                            "sqlite_count": 0,
                            "has_parquet": True,
                            "bag_count": bag_count,
                        }
                    else:
                        batch_map[batch_dir.name]["has_parquet"] = True
                        batch_map[batch_dir.name]["bag_count"] = bag_count
    except OSError as e:
        logger.warning("parquet 目录扫描失败（可能是 OSS FUSE 挂载断开）: %s", e)

    return list(batch_map.values())


@router.get("/resolve-bag-path")
async def resolve_bag_path(bag_id: str):
    """根据 bag_id 解析 bag 本地路径（用于前端可视化）。
    
    同时返回 em_bin 路径，3D BEV 视图需要从 em bin 目录读取 fusion_map_plus.bin。
    """
    try:
        from tools.rosbag_path_resolver import RosbagPathResolver
        resolver = RosbagPathResolver()
        
        # 先用 resolve_em_bin_path 一次性获取 rosbag 路径 + em bin 路径
        try:
            info = resolver.resolve_em_bin_path(bag_id)
        except Exception:
            # fallback: 如果 em bin 路径查询失败，至少返回 rosbag 路径
            info = resolver.resolve(bag_id)
        
        return {
            "bag_id": bag_id,
            "origin_bag_id": info.origin_bag_id,
            "bag_path": info.local_path or info.oss_path or "",
            "oss_path": info.oss_path or "",
            "local_path": info.local_path or "",
            "em_bin_oss_path": getattr(info, "em_bin_oss_path", None) or "",
            "em_bin_local_path": getattr(info, "em_bin_local_path", None) or "",
        }
    except ImportError:
        return {"bag_id": bag_id, "bag_path": "", "error": "dm_sdk not installed"}
    except Exception as e:
        return {"bag_id": bag_id, "bag_path": "", "error": str(e)}


def _paginate_rows(rows, page: int, page_size: int):
    """对结果行进行分页切片，返回 (page_rows, total_rows)。"""
    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], total


@router.post("/query", response_model=AgentQueryResponse)
async def agent_query(req: AgentQueryRequest):
    db_path, query_mode, resolved_batch_id = _resolve_db_path(req)

    validation_err = _validate_db_path(db_path, query_mode, resolved_batch_id)
    if validation_err:
        return AgentQueryResponse(sql="", explanation="", columns=[], rows=[], error=validation_err)

    # Handle oss:// paths
    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            return AgentQueryResponse(sql="", explanation="", columns=[], rows=[], error=str(e))

    logger.info("Agent query: %s | mode: %s | db: %s | batch_id: %s | limit: %s | page: %s",
                req.question, query_mode, db_path, resolved_batch_id, req.result_limit, req.page)
    engine = _get_engine(db_path, query_mode=query_mode, batch_id=resolved_batch_id)
    result = await engine.query(req.question, result_limit=req.result_limit, db_limit=req.db_limit, max_workers=req.max_workers)

    # 分页
    page = max(req.page, 1)
    page_size = max(req.page_size, 1)
    page_rows, total_rows = _paginate_rows(result.rows, page, page_size)

    return AgentQueryResponse(
        sql=result.sql,
        explanation=result.explanation,
        columns=result.columns,
        rows=page_rows,
        error=result.error,
        scanned_dbs=result.scanned_dbs,
        matched_dbs=result.matched_dbs,
        total_rows=total_rows,
        page=page,
        page_size=page_size,
    )


@router.post("/query-stream")
async def agent_query_stream(req: AgentQueryRequest):
    """SSE streaming endpoint for agent query progress."""
    db_path, query_mode, resolved_batch_id = _resolve_db_path(req)

    validation_err = _validate_db_path(db_path, query_mode, resolved_batch_id)
    if validation_err:
        async def err_gen():
            yield f"data: {json.dumps({'stage': 'error', 'message': validation_err})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            async def oss_err():
                yield f"data: {json.dumps({'stage': 'error', 'message': str(e)})}\n\n"
            return StreamingResponse(oss_err(), media_type="text/event-stream")

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=resolved_batch_id)
    page = max(req.page, 1)
    page_size = max(req.page_size, 1)

    async def event_generator():
        yield f"data: {json.dumps({'stage': 'understanding', 'message': '正在理解您的问题...'})}\n\n"

        # ── 使用 engine.query() 走两轮路径（Round1 Recipe + Round2 LLM + Round3 纠错） ──
        try:
            result = await engine.query(req.question, result_limit=req.result_limit, db_limit=req.db_limit, max_workers=req.max_workers)
        except Exception as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': f'查询失败: {e}'})}\n\n"
            return

        sql_source = getattr(result, 'sql_source', 'llm')
        correction_rounds = getattr(result, 'correction_rounds', 0)
        max_corrections_exceeded = getattr(result, 'max_corrections_exceeded', False)

        if sql_source == 'recipe':
            yield f"data: {json.dumps({'stage': 'recipe_hit', 'message': '匹配到场景模板，正在组装 SQL...', 'sql_source': 'recipe'})}\n\n"
        else:
            yield f"data: {json.dumps({'stage': 'generating', 'message': '使用 LLM 生成 SQL...', 'sql_source': 'llm'})}\n\n"

        yield f"data: {json.dumps({'stage': 'sql_generated', 'sql': result.sql, 'sql_source': sql_source, 'correction_rounds': correction_rounds, 'max_corrections_exceeded': max_corrections_exceeded})}\n\n"

        if result.error and max_corrections_exceeded:
            yield f"data: {json.dumps({'stage': 'error', 'message': result.error, 'sql': result.sql, 'correction_rounds': correction_rounds, 'max_corrections_exceeded': True})}\n\n"
            return

        if result.error:
            yield f"data: {json.dumps({'stage': 'error', 'message': result.error})}\n\n"
            return

        page_rows, total_rows = _paginate_rows(result.rows, page, page_size)
        yield f"data: {json.dumps({'stage': 'completed', 'sql': result.sql, 'explanation': result.explanation, 'columns': result.columns, 'rows': page_rows, 'error': result.error, 'scanned_dbs': result.scanned_dbs, 'matched_dbs': result.matched_dbs, 'total_rows': total_rows, 'page': page, 'page_size': page_size, 'correction_rounds': correction_rounds, 'max_corrections_exceeded': max_corrections_exceeded, 'sql_source': sql_source})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/execute-sql", response_model=AgentQueryResponse)
async def execute_sql(req: ExecuteSQLRequest):
    """直接执行用户提供的 SQL（不经 LLM）。"""
    db_path, query_mode, resolved_batch_id = _resolve_db_path(AgentQueryRequest(
        question="",
        db_path=req.db_path,
        batch_id=req.batch_id,
        query_mode=req.query_mode,
        db_limit=req.db_limit,
        result_limit=req.result_limit,
        page=req.page,
        page_size=req.page_size,
    ))

    validation_err = _validate_db_path(db_path, query_mode, resolved_batch_id)
    if validation_err:
        return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=validation_err)

    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=str(e))

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=resolved_batch_id)

    # SQL 校验
    validation_error = engine._validate_sql(req.sql)
    if validation_error:
        return AgentQueryResponse(sql=req.sql, explanation="SQL 校验失败", columns=[], rows=[], error=validation_error)

    try:
        if query_mode == "parquet":
            result = await engine._query_parquet(req.sql, result_limit=req.result_limit)
        elif engine.is_dir:
            result = await engine._query_batch(req.sql, result_limit=req.result_limit, db_limit=req.db_limit, max_workers=req.max_workers)
        else:
            result = await engine._query_single(req.sql, result_limit=req.result_limit)
    except Exception as e:
        return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=str(e))

    page = max(req.page, 1)
    page_size = max(req.page_size, 1)
    page_rows, total_rows = _paginate_rows(result.rows, page, page_size)

    return AgentQueryResponse(
        sql=result.sql,
        explanation=result.explanation,
        columns=result.columns,
        rows=page_rows,
        error=result.error,
        scanned_dbs=result.scanned_dbs,
        matched_dbs=result.matched_dbs,
        total_rows=total_rows,
        page=page,
        page_size=page_size,
    )


@router.post("/generate-sql")
async def generate_sql_only(req: AgentQueryRequest):
    """仅生成 SQL，不执行。返回生成的 SQL 供用户审查/修改。"""
    db_path, query_mode, resolved_batch_id = _resolve_db_path(req)

    validation_err = _validate_db_path(db_path, query_mode, resolved_batch_id)
    if validation_err:
        return {"sql": "", "error": validation_err}

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=resolved_batch_id)

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


@router.post("/execute-sql-arrow")
async def execute_sql_arrow(req: ExecuteSQLRequest):
    """执行 SQL 并以 Apache Arrow IPC 格式返回结果（二进制直传，大数据集更高效）。
    
    前端通过 fetch + ArrayBuffer 接收，使用 Apache Arrow JS 解析。
    如果 pyarrow 不可用则自动降级为 JSON。
    """
    try:
        import pyarrow as pa
    except ImportError:
        # 降级：走 JSON
        resp = await execute_sql(req)
        return resp

    db_path, query_mode, resolved_batch_id = _resolve_db_path(AgentQueryRequest(
        question="",
        db_path=req.db_path,
        batch_id=req.batch_id,
        query_mode=req.query_mode,
        db_limit=req.db_limit,
        result_limit=req.result_limit,
    ))

    validation_err = _validate_db_path(db_path, query_mode, resolved_batch_id)
    if validation_err:
        # 返回 JSON 错误
        return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=validation_err)

    if db_path.startswith("oss://"):
        from tools.rosbag_path_resolver import resolve_db_path
        try:
            db_path = resolve_db_path(db_path)
        except ValueError as e:
            return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=str(e))

    engine = _get_engine(db_path, query_mode=query_mode, batch_id=resolved_batch_id)

    validation_error = engine._validate_sql(req.sql)
    if validation_error:
        return AgentQueryResponse(sql=req.sql, explanation="SQL 校验失败", columns=[], rows=[], error=validation_error)

    try:
        if query_mode == "parquet":
            result = await engine._query_parquet(req.sql, result_limit=req.result_limit)
        elif engine.is_dir:
            result = await engine._query_batch(req.sql, result_limit=req.result_limit, db_limit=req.db_limit, max_workers=req.max_workers)
        else:
            result = await engine._query_single(req.sql, result_limit=req.result_limit)
    except Exception as e:
        return AgentQueryResponse(sql=req.sql, explanation="", columns=[], rows=[], error=str(e))

    # 将 rows (List[Dict]) 转为 Arrow Table
    if not result.rows:
        # 空结果：返回空 Arrow 表
        table = pa.table({})
    else:
        # 按列组织数据
        columns_data = {col: [] for col in result.columns}
        for row in result.rows:
            for col in result.columns:
                columns_data[col].append(row.get(col))
        
        arrow_arrays = []
        arrow_fields = []
        for col in result.columns:
            values = columns_data[col]
            # 推断类型
            try:
                arr = pa.array(values)
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                # 降级为 string
                arr = pa.array([str(v) if v is not None else None for v in values], type=pa.string())
            arrow_fields.append(pa.field(col, arr.type))
            arrow_arrays.append(arr)
        
        schema = pa.schema(arrow_fields)
        table = pa.table({field.name: arr for field, arr in zip(arrow_fields, arrow_arrays)}, schema=schema)

    # 序列化为 Arrow IPC (stream format)
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()

    return Response(
        content=sink.getvalue().to_pybytes(),
        media_type="application/vnd.apache.arrow.stream",
        headers={
            "X-Arrow-Schema": json.dumps({"fields": [{"name": f.name, "type": str(f.type)} for f in table.schema]}),
            "X-Total-Rows": str(len(result.rows)),
            "X-SQL": result.sql[:500],
        },
    )
