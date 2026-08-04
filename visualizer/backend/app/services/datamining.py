"""DataMining 平台同步服务 — 评测集上传 + 策略 save-or-update + 策略评测详情

端点（网关前缀 settings.DATAMINING_BASE_URL）：
  POST {base}/evalset/benchmark/upload            评测集批量上传（幂等去重）
  POST {base}/api/text2sql/strategy/save          策略新建（重名返回 code=409）
  GET  {base}/api/text2sql/strategy/search        按名查策略 id
  POST {base}/api/text2sql/strategy/update/{id}   策略更新
  POST {base}/api/text2sql/strategy/review        策略评测记录(评测详情，通过/不通过)
"""
import logging
from typing import Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _token() -> str:
    return settings.EVAL_SYNC_TOKEN or settings.DM_ACCESS_TOKEN or ""


def _headers() -> Dict[str, str]:
    return {"Content-Type": "application/json", "Access-Token": _token()}


async def upload_evalset(benchmark_name: str, label_res_list: List[Dict]) -> Dict:
    """批量上传评测条目。返回产线响应 data（successCount/failCount/details）。"""
    url = f"{settings.DATAMINING_BASE_URL}/evalset/benchmark/upload"
    payload = {"benchmark_name": benchmark_name, "label_res_list": label_res_list}
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        resp = await client.post(url, headers=_headers(), json=payload)
    body = resp.json()
    if resp.status_code != 200 or body.get("code") != 200:
        raise RuntimeError(f"产线 upload 失败: {body.get('message') or resp.text}")
    return body.get("data") or {}


async def sync_strategy_to_dm(
    name: str,
    tag_name: Optional[str],
    sql: str,
    description: Optional[str],
) -> Dict:
    """同步策略到 DataMining sql_strategy 表（save-or-update）。

    返回 {"mode": "created"|"updated", "id": ...}
    """
    base = f"{settings.DATAMINING_BASE_URL}/api/text2sql"
    dto = {
        "strategyName": name,
        "tagName": tag_name or "",
        "description": description or "",
        "sqlContent": sql,
        "createBy": "scenesql",
        "updateBy": "scenesql",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        resp = await client.post(f"{base}/strategy/save", headers=_headers(), json=dto)
        body = resp.json()
        if body.get("code") == 200:
            return {"mode": "created", "id": (body.get("data") or {}).get("id")}
        if body.get("code") != 409:
            raise RuntimeError(f"产线 strategy/save 失败: {body.get('message') or resp.text}")

        # 重名 → 查 id 后更新
        search = await client.get(f"{base}/strategy/search", headers=_headers(), params={"keyword": name})
        sbody = search.json()
        items = sbody.get("data") or []
        target = next((s for s in items if s.get("strategyName") == name), None)
        if not target:
            raise RuntimeError(f"策略重名(409)但按名搜索未命中: {name}")
        upd = await client.post(f"{base}/strategy/update/{target['id']}", headers=_headers(), json=dto)
        ubody = upd.json()
        if ubody.get("code") != 200:
            raise RuntimeError(f"产线 strategy/update 失败: {ubody.get('message') or upd.text}")
        return {"mode": "updated", "id": target["id"]}


async def sync_strategy_reviews(strategy_id: int, cases: List[Dict]) -> Dict:
    """把标注 case 推送为 DataMining 策略评测记录（评测详情：哪些 case 通过/不通过）。

    verdict pass→reviewResult 1，fail→2。bag_id 同时作为 bagId 与 dataId
    （SceneSQL 的 bag_id 即 em_bin/模块 bin）。时间戳用秒。dataVersion 固定
    "v1"：产线去重 SQL 为 data_version = #{dataVersion}，NULL 永不匹配会导致
    重复插入，故必须传稳定非空值。产线按 (strategyId,bagId,dataVersion,startTs)
    幂等 upsert，可重复同步。返回 {"pushed", "skipped", "failed"}。
    """
    base = f"{settings.DATAMINING_BASE_URL}/api/text2sql"
    pushed = skipped = failed = 0
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        for c in cases:
            if c.get("start_ts") is None or c.get("end_ts") is None:
                skipped += 1
                continue
            dto = {
                "strategyId": strategy_id,
                "bagId": c.get("bag_id"),
                "dataId": c.get("bag_id"),
                "dataVersion": "v1",
                "startTs": int(c["start_ts"]),
                "endTs": int(c["end_ts"]),
                "reviewResult": 1 if c.get("verdict") == "pass" else 2,
                "reviewer": "scenesql",
            }
            try:
                resp = await client.post(f"{base}/strategy/review", headers=_headers(), json=dto)
                body = resp.json()
            except Exception as e:
                logger.warning("strategy/review 请求异常: %s", e)
                failed += 1
                continue
            if body.get("code") == 200:
                pushed += 1
            else:
                failed += 1
                logger.warning("strategy/review 失败: %s", body.get("message"))
    return {"pushed": pushed, "skipped": skipped, "failed": failed}
