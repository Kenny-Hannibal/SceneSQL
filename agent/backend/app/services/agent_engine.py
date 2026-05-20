#!/usr/bin/env python3
"""Agent Engine — NL2SQL 核心逻辑（支持批量查询 & bag_id 反查）。"""

import os
import re
import sqlite3
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import asyncio
import logging

from agent.backend.app.core.schema_reader import read_schema, format_schema_for_prompt
from agent.backend.app.core.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
你是一个 ROS 数据查询助手。根据用户的问题生成 SQLite SQL。

数据库是 SQLite，包含 rosbag 解析后的标签数据和车辆状态数据。

重要规则：
1. 只使用 Schema 中存在的表和字段
2. 时间字段：range_tag 表使用秒（start_ts/end_ts），ego/dynamic_obj 表使用纳秒（ts）
3. range_tag.param 是 JSON 字段，需要用 json_extract(param, '$.key') 提取
4. 跨表查询时，用 ts（纳秒）或 ts_ms（毫秒）关联时间
5. 输出必须是纯 SQL，不要包含 markdown 代码块标记，不要包含任何解释文字
6. 如果用户问题无法回答，返回 "SELECT '无法回答' AS reason;"
7. 优先使用 range_tag 表查找场景标签片段，因为它已经预计算了场景起止时间
8. 涉及 dynamic_obj 时，X 轴向前为正，Y 轴向左为正
9. SQL 必须完整，必须包含 SELECT、FROM，必要时包含 WHERE
10. **不要生成 LIMIT 子句**，LIMIT 由系统自动注入
"""


@dataclass
class AgentResult:
    sql: str
    explanation: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    error: Optional[str] = None
    scanned_dbs: int = 0
    matched_dbs: int = 0


class AgentEngine:
    def __init__(self, db_path: str = ""):
        self.query_mode = os.environ.get("QUERY_MODE", "sqlite")
        self.db_path = db_path
        self.is_dir = os.path.isdir(db_path) if db_path else False
        self._parquet_conn = None
        self._resolver = None
        self._resolver_lock = asyncio.Lock()
        self.llm = LLMClient()

        if self.query_mode == "parquet":
            self._init_parquet_mode()
        else:
            self._init_sqlite_mode(db_path)

    def _init_sqlite_mode(self, db_path: str):
        """SQLite 模式：逐个连接 SQLite DB 查询。"""
        self.sample_db = self._get_sample_db()
        self.schema = read_schema(self.sample_db)
        self.schema_text = format_schema_for_prompt(self.schema)

    def _init_parquet_mode(self):
        """Parquet 模式：通过 DuckDB 查询聚合后的 Parquet 文件。"""
        from agent.backend.app.services.etl import get_manager
        self.etl_manager = get_manager()
        self._parquet_conn = self.etl_manager.get_connection()
        self.schema = read_schema("", conn=self._parquet_conn)
        self.schema_text = format_schema_for_prompt(self.schema)

    def _get_sample_db(self) -> str:
        if not self.is_dir:
            return self.db_path
        try:
            dbs = [f for f in os.listdir(self.db_path) if f.endswith(".db")]
        except OSError as exc:
            raise ValueError(f"无法读取目录 {self.db_path}: {exc}") from exc
        if not dbs:
            raise ValueError(f"No .db files found in {self.db_path}")
        return os.path.join(self.db_path, dbs[0])

    async def _get_resolver(self):
        if self._resolver is None:
            async with self._resolver_lock:
                if self._resolver is None:
                    from tools.rosbag_path_resolver import RosbagPathResolver
                    self._resolver = RosbagPathResolver()
        return self._resolver

    def _clean_sql(self, raw: str) -> str:
        raw = raw.strip()
        # Remove <think>...</think> blocks (reasoning content)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        # If </think> remains (incomplete tag), drop everything before the last </think>
        if "</think>" in raw:
            raw = raw.split("</think>")[-1]
        # Extract content from markdown code blocks
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0] if "\n" in raw else raw
        raw = raw.strip()
        if raw.lower().startswith("sql"):
            raw = raw[3:].strip()
        # Find the last line that looks like a SQL statement
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        for line in reversed(lines):
            upper = line.upper()
            if upper.startswith("SELECT") or upper.startswith("WITH"):
                return line
        # Fallback: return the whole text
        return raw

    def _validate_sql(self, sql: str) -> Optional[str]:
        upper = sql.upper().strip()
        if not upper.startswith("SELECT"):
            return "只允许 SELECT 查询"
        if " FROM " not in upper and "\nFROM " not in upper:
            return "SQL 不完整，缺少 FROM 子句"
        forbidden = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE"]
        for kw in forbidden:
            if kw in upper:
                return f"禁止执行 {kw}"
        return None

    def _inject_limit(self, sql: str, limit: int) -> str:
        """Inject LIMIT if not present. Replace if present."""
        import re
        # Remove existing LIMIT clause (case insensitive)
        cleaned = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+LIMIT\s+\d+\s*;?\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if not cleaned.endswith(';'):
            cleaned += ';'
        # Append limit before the final semicolon
        return re.sub(r';\s*$', f' LIMIT {limit};', cleaned)

    def _execute_single(self, sql: str, db_path: str, db_file: str, resolver) -> List[Dict[str, Any]]:
        """Execute SQL on a single DB and return rows with bag_id & bag_path appended."""
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql)
            rows = cursor.fetchall()
            if not rows:
                conn.close()
                return []

            # Resolve bag_id & local path
            data_id = db_file.replace(".db", "")
            bag_id = data_id
            bag_path = ""
            try:
                info = resolver.resolve(data_id)
                bag_id = info.origin_bag_id or data_id
                bag_path = info.local_path or info.oss_path or ""
            except Exception as exc:
                logger.debug("Resolver failed for %s: %s", data_id, exc)

            result = []
            for row in rows:
                d = dict(row)
                d["bag_id"] = bag_id
                d["db_file"] = db_file
                d["bag_path"] = bag_path
                result.append(d)
            conn.close()
            return result
        except Exception as exc:
            logger.warning("SQL execution failed on %s: %s", db_file, exc)
            return [{"_error": str(exc), "db_file": db_file}]

    def _execute_parquet(self, sql: str, result_limit: int) -> List[Dict[str, Any]]:
        """在 Parquet 聚合数据上执行 SQL（DuckDB），返回带 bag_id 的结果。"""
        try:
            if self._parquet_conn is None:
                raise RuntimeError("Parquet 连接未初始化")
            cursor = self._parquet_conn.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            result = []
            for row in rows:
                d = dict(zip(columns, row))
                # Parquet 中已有 bag_id 列，无需额外解析
                if "bag_id" not in d:
                    d["bag_id"] = ""
                result.append(d)
            return result
        except Exception as exc:
            logger.warning("Parquet SQL execution failed: %s", exc)
            return [{"_error": str(exc)}]

    async def _query_batch(self, sql: str, result_limit: int = 100, db_limit: int = 30, max_workers: int = 32) -> AgentResult:
        sql = self._inject_limit(sql, result_limit)
        try:
            db_files = sorted([f for f in os.listdir(self.db_path) if f.endswith(".db")])
        except Exception as exc:
            return AgentResult(sql=sql, explanation="无法列出目录下的 .db 文件", error=str(exc))
        total = len(db_files)
        if total == 0:
            return AgentResult(sql=sql, explanation="目录下没有 .db 文件", error="No DB files found")
        db_files = db_files[:db_limit]

        resolver = await self._get_resolver()
        loop = asyncio.get_event_loop()

        all_rows: List[Dict[str, Any]] = []
        errors: List[str] = []
        columns: List[str] = []
        matched = 0

        def process_one(db_file: str):
            db_path = os.path.join(self.db_path, db_file)
            return self._execute_single(sql, db_path, db_file, resolver)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [loop.run_in_executor(executor, process_one, f) for f in db_files]
            for rows in await asyncio.gather(*futures):
                for row in rows:
                    if "_error" in row:
                        errors.append(f"{row['db_file']}: {row['_error']}")
                    else:
                        all_rows.append(row)
                        if not columns:
                            columns = list(row.keys())
                if rows and "_error" not in rows[0]:
                    matched += 1

        explanation = f"共扫描 {total} 个 DB，{matched} 个有命中，返回 {len(all_rows)} 条记录"
        error_msg = None
        if errors:
            error_msg = f"{len(errors)} 个 DB 执行失败（仅展示前 3 条）: " + "; ".join(errors[:3])

        return AgentResult(
            sql=sql,
            explanation=explanation,
            rows=all_rows,
            columns=columns,
            error=error_msg,
            scanned_dbs=total,
            matched_dbs=matched,
        )

    async def _query_parquet(self, sql: str, result_limit: int = 100) -> AgentResult:
        """Parquet 模式：在聚合后的 Parquet 上执行单次查询。"""
        sql = self._inject_limit(sql, result_limit)
        rows = self._execute_parquet(sql, result_limit)
        errors = [r for r in rows if "_error" in r]
        good_rows = [r for r in rows if "_error" not in r]
        columns = list(good_rows[0].keys()) if good_rows else []

        # 去重统计 bag_id
        bag_ids = set()
        for r in good_rows:
            if "bag_id" in r and r["bag_id"]:
                bag_ids.add(r["bag_id"])

        return AgentResult(
            sql=sql,
            explanation=f"Parquet 聚合查询，命中 {len(bag_ids)} 个 bag，返回 {len(good_rows)} 条记录",
            rows=good_rows,
            columns=columns,
            error=f"{len(errors)} 个错误" if errors else None,
            scanned_dbs=len(bag_ids),
            matched_dbs=len(bag_ids),
        )

    async def _query_single(self, sql: str, result_limit: int = 100) -> AgentResult:
        sql = self._inject_limit(sql, result_limit)
        db_file = os.path.basename(self.db_path)
        resolver = await self._get_resolver()

        rows = self._execute_single(sql, self.db_path, db_file, resolver)
        errors = [r for r in rows if "_error" in r]
        good_rows = [r for r in rows if "_error" not in r]
        columns = list(good_rows[0].keys()) if good_rows else []
        return AgentResult(
            sql=sql,
            explanation="基于 Schema 生成并执行 SQL",
            rows=good_rows,
            columns=columns,
            error=f"{len(errors)} 个 DB 执行失败" if errors else None,
            scanned_dbs=1,
            matched_dbs=1 if good_rows else 0,
        )

    async def query(self, question: str, result_limit: int = 100, db_limit: int = 30) -> AgentResult:
        prompt = f"""
{self.schema_text}

用户问题：{question}

请生成 SQLite SQL（只输出纯 SQL，不要解释，不要带 LIMIT）：
"""
        raw_sql = await self.llm.chat(SYSTEM_PROMPT, prompt, temperature=0.1)
        sql = self._clean_sql(raw_sql)

        validation_error = self._validate_sql(sql)
        if validation_error:
            return AgentResult(
                sql=sql,
                explanation="SQL 校验失败",
                error=validation_error,
            )

        if self.query_mode == "parquet":
            return await self._query_parquet(sql, result_limit=result_limit)
        elif self.is_dir:
            return await self._query_batch(sql, result_limit=result_limit, db_limit=db_limit)
        else:
            return await self._query_single(sql, result_limit=result_limit)


class _DummyResolver:
    """Fallback resolver when dm_sdk is unavailable."""
    def resolve(self, data_id: str):
        class _Info:
            origin_bag_id = data_id
        return _Info()
