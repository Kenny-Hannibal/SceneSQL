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
from agent.backend.app.core.tag_router import TagRouter, build_prompt, RouteResult

logger = logging.getLogger(__name__)


# 旧的全量 SYSTEM_PROMPT 已迁移到 tag_router.py 的 SYSTEM_PROMPT_TEMPLATE
# 保留此常量作为 fallback（路由失败时使用）
FALLBACK_SYSTEM_PROMPT = """
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
    def __init__(self, db_path: str = "", query_mode: str = ""):
        self.query_mode = query_mode or os.environ.get("QUERY_MODE", "sqlite")
        self.db_path = db_path
        self.is_dir = os.path.isdir(db_path) if db_path else False
        self._parquet_conn = None
        self._resolver = None
        self._resolver_lock = asyncio.Lock()
        self.llm = LLMClient()

        # P0: 关键词路由器
        self.router = TagRouter()

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

    @staticmethod
    def _adapt_sql_for_duckdb(sql: str) -> str:
        """将 SQLite 风格 SQL 适配为 DuckDB 兼容语法。

        已知差异点：
        1. EXISTS 返回 boolean (true/false)，而 SQLite 返回 integer (1/0)
           → 将 boolean 上下文中的 = 1 改为 = true, = 0 改为 = false
        2. group_concat → string_agg
        3. strftime('%Y-%m-%d', col) → strftime(col, '%Y-%m-%d')  (参数顺序)
        4. json_extract → json_extract_string
           SQLite 的 json_extract 直接返回标量值（如 锥桶），
           DuckDB 的 json_extract 返回 JSON 原生表示（如 "锥桶" 带引号），
           json_extract_string 才返回与 SQLite 一致的纯文本。
        """
        # 1. boolean 比较适配：匹配常见 boolean 列命名模式
        #    has_xxx = 1 → has_xxx = true
        #    is_xxx = 1 → is_xxx = true
        #    xxx_flag = 1 → xxx_flag = true
        bool_col_pattern = r'(\b(?:has|is|can|should|has_\w+|is_\w+|\w*_flag|\w*_bool|\w*_check))\s*=\s*1\b'
        sql = re.sub(bool_col_pattern, r'\1 = true', sql)
        bool_col_zero = r'(\b(?:has|is|can|should|has_\w+|is_\w+|\w*_flag|\w*_bool|\w*_check))\s*=\s*0\b'
        sql = re.sub(bool_col_zero, r'\1 = false', sql)

        # 2. group_concat → string_agg
        sql = re.sub(r'\bgroup_concat\b', 'string_agg', sql, flags=re.IGNORECASE)

        # 3. strftime 参数顺序：SQLite strftime(fmt, col) → DuckDB strftime(col, fmt)
        #    匹配 strftime('%...', col) 并交换参数
        def _swap_strftime(m):
            fmt = m.group(1)
            col = m.group(2)
            return f"strftime({col}, {fmt})"
        sql = re.sub(
            r"strftime\s*\(\s*('[^']*')\s*,\s*([^)]+)\s*\)",
            _swap_strftime, sql
        )

        # 4. json_extract → json_extract_string
        #    DuckDB json_extract 返回 JSON 类型值，比较时会把右值也当 JSON 解析导致报错
        #    json_extract_string 返回 VARCHAR，行为与 SQLite 的 json_extract 一致
        sql = re.sub(r'\bjson_extract\b', 'json_extract_string', sql, flags=re.IGNORECASE)

        return sql

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
        # ── 提取完整SQL ──
        # 策略：去markdown标记后，如果文本以SELECT/WITH开头，直接返回全文（多行SQL含子查询）
        # 仅在LLM混入解释文字时，才用逐行回退策略找SQL起始行并拼接后续行
        lines = [line.strip() for line in raw.splitlines() if line.strip()]

        # 优先：首行就是SQL开头 → 返回全文
        if lines:
            first_upper = lines[0].upper()
            if first_upper.startswith("SELECT") or first_upper.startswith("WITH"):
                return raw

        # 回退：找最后一个SELECT/WITH开头的行，从该行开始拼接（处理LLM先输出解释再输出SQL的情况）
        for i in range(len(lines) - 1, -1, -1):
            upper = lines[i].upper()
            if upper.startswith("SELECT") or upper.startswith("WITH"):
                return "\n".join(lines[i:])

        # 最终回退：返回全文
        return raw

    def _validate_sql(self, sql: str) -> Optional[str]:
        import re
        # 去掉行注释和块注释后再判断语句类型
        cleaned = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        cleaned = re.sub(r"--.*?\n", "\n", cleaned)
        upper = cleaned.upper().strip()
        if not (upper.startswith("SELECT") or upper.startswith("WITH")):
            return "只允许 SELECT 查询（含 WITH CTE）"
        if " FROM " not in upper and "\nFROM " not in upper:
            return "SQL 不完整，缺少 FROM 子句"
        forbidden = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE"]
        for kw in forbidden:
            if kw in upper:
                return f"禁止执行 {kw}"
        # P0 增强：检查 SQL 中引用的表名是否存在于 schema
        known_tables = {t.name for t in self.schema}
        for word in re.findall(r'\b\w+\b', sql):
            if word.lower() in known_tables or word in known_tables:
                continue
            # 检查 FROM/JOIN 后面的词是否是已知表
        return None

    def _inject_limit(self, sql: str, limit: int) -> str:
        """Inject LIMIT if not present. Replace if present.

        limit <= 0 表示不限制结果数量，直接返回原始 SQL（不注入 LIMIT）。
        """
        import re
        if limit <= 0:
            return sql
        # Remove existing LIMIT clause (case insensitive)
        cleaned = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+LIMIT\s+\d+\s*;?\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if not cleaned.endswith(';'):
            cleaned += ';'
        # Append limit before the final semicolon
        return re.sub(r';\s*$', f' LIMIT {limit};', cleaned)

    def _execute_single(self, sql: str, db_path: str, db_file: str) -> List[Dict[str, Any]]:
        """Execute SQL on a single DB and return rows with bag_id prepended.

        bag_id 直接使用 .db 文件名（去掉后缀），不再倒查原始 bag id。
        """
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql)
            rows = cursor.fetchall()
            if not rows:
                conn.close()
                return []

            bag_id = db_file.replace(".db", "")
            result = []
            for row in rows:
                d = {"bag_id": bag_id, **dict(row)}
                result.append(d)
            conn.close()
            return result
        except Exception as exc:
            logger.warning("SQL execution failed on %s: %s", db_file, exc)
            return [{"_error": str(exc), "db_file": db_file}]

    def _execute_parquet(self, sql: str, result_limit: int) -> List[Dict[str, Any]]:
        """在 Parquet 聚合数据上执行 SQL（DuckDB），返回原始行。"""
        sql = self._adapt_sql_for_duckdb(sql)
        try:
            if self._parquet_conn is None:
                raise RuntimeError("Parquet 连接未初始化")
            cursor = self._parquet_conn.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            result = []
            for row in rows:
                d = dict(zip(columns, row))
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
        # 不再限制扫描的 DB 数量；db_limit 参数仅保留以兼容旧 API，实际扫描全部 .db

        resolver = await self._get_resolver()
        loop = asyncio.get_event_loop()

        all_rows: List[Dict[str, Any]] = []
        errors: List[str] = []
        columns: List[str] = []
        matched = 0

        # ── P1: 单 DB 查询 ──
        # bag_id 直接使用 .db 文件名（去掉后缀），不再调用 resolver 倒查原始 bag。
        # 返回字段中不再包含 db_file / bag_path；bag_id 放在字典最前面。
        def process_one(db_file: str):
            bag_id = db_file.replace(".db", "")
            db_path = os.path.join(self.db_path, db_file)
            conn = None
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                # 只读连接不要设置 journal_mode=WAL，FUSE/只读文件系统会报
                # "attempt to write a readonly database"
                # cache_size 和 mmap_size 是安全的只读优化
                conn.execute("PRAGMA cache_size=-64000")
                conn.execute("PRAGMA mmap_size=268435456")
                cursor = conn.execute(sql)
                rows = cursor.fetchall()
                if not rows:
                    conn.close()
                    return []

                result = []
                for row in rows:
                    d = {"bag_id": bag_id, **dict(row)}
                    result.append(d)
                conn.close()
                return result
            except Exception as exc:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                logger.warning("SQL execution failed on %s: %s", db_file, exc)
                return [{"_error": str(exc), "db_file": db_file}]

        # ── P2: 并行执行查询（提前退出，限制并发）──
        # 不再一次性提交所有 DB 的任务，而是按 max_workers 批次启动，
        # 收集够 result_limit 后立即停止启动新任务，避免连接/线程爆炸。
        # result_limit <= 0 表示不限制结果数量，此时遍历所有 DB。
        unlimited = result_limit <= 0
        effective_limit = result_limit if not unlimited else float('inf')
        executor = ThreadPoolExecutor(max_workers=max_workers)

        def run_one(f: str):
            return loop.run_in_executor(executor, process_one, f)

        pending: set[asyncio.Future] = set()
        remaining = list(db_files)
        stopped = False

        async def wait_one():
            nonlocal stopped
            # 保持最多 max_workers 个并发任务
            while len(pending) < max_workers and remaining and not stopped:
                pending.add(run_one(remaining.pop(0)))
            if not pending:
                return None
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            completed = next(iter(done))
            pending.discard(completed)
            return await completed

        try:
            while True:
                if not unlimited and len(all_rows) >= effective_limit:
                    stopped = True
                    break
                rows = await wait_one()
                if rows is None:
                    break
                for row in rows:
                    if "_error" in row:
                        errors.append(f"{row['db_file']}: {row['_error']}")
                    else:
                        all_rows.append(row)
                        if not columns:
                            columns = list(row.keys())
                        if not unlimited and len(all_rows) >= effective_limit:
                            stopped = True
                            break
                if rows and "_error" not in rows[0]:
                    matched += 1
        finally:
            # 取消还未开始执行的任务
            for fut in pending:
                fut.cancel()
            # 等待已在执行的任务尽快结束，最多等 2 秒
            if pending:
                await asyncio.wait(pending, timeout=2)
            # 不等待正在执行的任务，立即释放线程池
            executor.shutdown(wait=False)

        # 截断到 result_limit（仅在有限制时）
        if not unlimited:
            all_rows = all_rows[:result_limit]
            explanation = f"共扫描 {total} 个 DB，{matched} 个有命中，返回 {len(all_rows)} 条记录（ LIMIT {result_limit} 提前终止）"
        else:
            explanation = f"共扫描 {total} 个 DB，{matched} 个有命中，返回 {len(all_rows)} 条记录（无结果数量限制）"
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

    @staticmethod
    def _ensure_bag_id_in_select(sql: str) -> str:
        """如果 SQL 中没有 bag_id，通过 AST 自动注入（支持 CTE 链和 GROUP BY）。

        对 CTE 中的每个 SELECT 以及最外层 SELECT 都注入 bag_id，
        同时把 bag_id 追加到 GROUP BY 列表，避免语法错误。

        当 SELECT 的 FROM 子句包含多个表（JOIN）时，bag_id 会出现歧义引用。
        此时自动给 bag_id 加上第一个表的别名/名称前缀，如 ``e.bag_id``。
        """
        upper = sql.upper()
        if "SELECT *" in upper:
            return sql
        # 纯聚合查询（无 GROUP BY 但有聚合函数）不添加 bag_id，避免语法错误
        has_aggregate = any(agg in upper for agg in ["COUNT(", "SUM(", "AVG(", "MAX(", "MIN("])
        has_group_by = "GROUP BY" in upper
        if has_aggregate and not has_group_by:
            return sql

        try:
            import sqlglot
            from sqlglot import exp
            tree = sqlglot.parse_one(sql, dialect="duckdb")
        except Exception:
            # AST 解析失败，回退到原始 SQL（不注入 bag_id）
            return sql

        def _get_bag_id_col(select_node: sqlglot.exp.Select):
            """根据当前 SELECT 的 FROM/JOIN 表数量决定 bag_id 的注入形式。"""
            tables = []
            from_node = select_node.args.get("from_")
            if from_node:
                for t in from_node.find_all(exp.Table):
                    tables.append(t)
            joins = select_node.args.get("joins") or []
            for join in joins:
                for t in join.find_all(exp.Table):
                    tables.append(t)

            if len(tables) == 0:
                return None
            if len(tables) == 1:
                return exp.column("bag_id")
            # 多表 JOIN：使用第一个表的别名/名称前缀避免歧义
            first = tables[0]
            ref = first.alias or first.name
            return exp.column("bag_id", table=ref)

        modified = False
        for select in tree.find_all(exp.Select):
            # 检查当前 SELECT 是否已经有 bag_id
            has_bag_id = any(
                isinstance(e, exp.Column) and e.name.lower() == "bag_id"
                for e in select.expressions
            )
            if has_bag_id:
                continue

            bag_id_col = _get_bag_id_col(select)
            if bag_id_col is None:
                continue

            # 注入 bag_id 到 SELECT 列表最前面
            select.set("expressions", [bag_id_col] + select.expressions)
            modified = True

            # 如果有 GROUP BY，也注入 bag_id
            group = select.args.get("group")
            if group and hasattr(group, "expressions"):
                has_bag_id_in_group = any(
                    isinstance(e, exp.Column) and e.name.lower() == "bag_id"
                    for e in group.expressions
                )
                if not has_bag_id_in_group:
                    group.expressions.insert(0, bag_id_col.copy())

        if not modified:
            return sql

        return tree.sql(dialect="duckdb")

    async def _query_parquet(self, sql: str, result_limit: int = 100) -> AgentResult:
        """Parquet 模式：在聚合后的 Parquet 上执行单次查询，注入 bag_id。"""
        original_sql = sql
        sql_with_bag_id = self._ensure_bag_id_in_select(sql)
        sql_with_bag_id = self._inject_limit(sql_with_bag_id, result_limit)
        rows = self._execute_parquet(sql_with_bag_id, 0 if result_limit <= 0 else result_limit)
        errors = [r for r in rows if "_error" in r]

        # 如果 bag_id 注入导致报错，回退到原始 SQL（不含 bag_id）
        if errors and any("bag_id" in r.get("_error", "").lower() for r in errors):
            sql = self._inject_limit(original_sql, result_limit)
            rows = self._execute_parquet(sql, result_limit)
            errors = [r for r in rows if "_error" in r]
        else:
            sql = sql_with_bag_id

        good_rows = [r for r in rows if "_error" not in r]

        # 收集所有唯一 bag_id
        bag_id_set = set()
        for r in good_rows:
            bid = r.get("bag_id")
            if bid:
                bag_id_set.add(bid)

        # 确保 bag_id 始终是第一列（Parquet 模式 SQL 中可能已有 bag_id）
        for i, r in enumerate(good_rows):
            if "bag_id" in r:
                good_rows[i] = {"bag_id": r.pop("bag_id"), **r}

        columns = list(good_rows[0].keys()) if good_rows else []

        error_msg = None
        if errors:
            error_details = "; ".join(r.get("_error", "unknown") for r in errors)
            error_msg = f"{len(errors)} 个错误: {error_details}"

        return AgentResult(
            sql=sql,
            explanation=f"Parquet 聚合查询，命中 {len(bag_id_set)} 个 bag，返回 {len(good_rows)} 条记录",
            rows=good_rows,
            columns=columns,
            error=error_msg,
            scanned_dbs=len(bag_id_set),
            matched_dbs=len(bag_id_set),
        )

    async def _query_single(self, sql: str, result_limit: int = 100) -> AgentResult:
        sql = self._inject_limit(sql, result_limit)
        db_file = os.path.basename(self.db_path)

        rows = self._execute_single(sql, self.db_path, db_file)
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

    async def query(self, question: str, result_limit: int = 100, db_limit: int = 30, max_workers: int = 32) -> AgentResult:
        # ── P0: 路由 + 分层注入 ──
        route = self.router.route(question)

        # Layer 1: 只输出路由命中表的 Schema
        schema_text = format_schema_for_prompt(
            self.schema,
            only_tables=route.involved_tables if route.involved_tables else None,
        )

        # 组装 system_prompt + user_prompt（含 Layer 0 Schema Card + Layer 2 标签语义 + few-shot）
        system_prompt, user_prompt = build_prompt(
            question=question,
            schema_text=schema_text,
            route=route,
        )

        logger.info(
            "Route: method=%s tags=%s tables=%s",
            route.method,
            [t.tag_name for t in route.matched_tags],
            sorted(route.involved_tables),
        )

        raw_sql = await self.llm.chat(system_prompt, user_prompt, temperature=0.1)
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
            return await self._query_batch(sql, result_limit=result_limit, db_limit=db_limit, max_workers=max_workers)
        else:
            return await self._query_single(sql, result_limit=result_limit)


class _DummyResolver:
    """Fallback resolver when dm_sdk is unavailable."""
    def resolve(self, data_id: str):
        class _Info:
            origin_bag_id = data_id
        return _Info()
