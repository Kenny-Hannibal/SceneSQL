"""评测标注 API — 通过/不通过 case 标注与产线评测集同步

标注按 (strategy, bag_id, start_ts, end_ts) 去重 upsert，存 JSONL。
同步时转换为产线格式：bin_id=row.bag_id（SceneSQL 查询行的 bag_id 即
ubm_vehicle_module_bin 的 data_id）、tag=<策略名>_positive/_negative、
时间戳秒→纳秒。
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.eval_case_store import eval_case_store
from app.services.datamining import upload_evalset

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/eval-labels", tags=["eval-labels"])


class LabelRequest(BaseModel):
    strategy_name: str
    bag_id: str
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    verdict: str  # "pass" | "fail"


class SyncRequest(BaseModel):
    benchmark_name: str


@router.post("")
async def add_label(req: LabelRequest):
    if req.verdict not in ("pass", "fail"):
        raise HTTPException(status_code=400, detail="verdict must be 'pass' or 'fail'")
    case = eval_case_store.add_case(
        req.strategy_name, req.bag_id, req.start_ts, req.end_ts, req.verdict
    )
    return {"ok": True, "case": case}


@router.delete("/{strategy_name}")
async def delete_label(strategy_name: str, bag_id: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None):
    removed = eval_case_store.remove_case(strategy_name, bag_id, start_ts, end_ts)
    if not removed:
        raise HTTPException(status_code=404, detail="label not found")
    return {"ok": True}


@router.get("/{strategy_name}")
async def list_labels(strategy_name: str):
    return {"strategy_name": strategy_name, "cases": eval_case_store.list_cases(strategy_name)}


@router.post("/{strategy_name}/sync-evalset")
async def sync_evalset(strategy_name: str, req: SyncRequest):
    """把该策略的全部标注同步到产线评测集（幂等，可重复执行）。"""
    cases = eval_case_store.list_cases(strategy_name)
    if not cases:
        raise HTTPException(status_code=400, detail="该策略暂无标注 case")

    label_res_list = []
    skipped = []
    for c in cases:
        if c.get("start_ts") is None or c.get("end_ts") is None:
            skipped.append({"bag_id": c.get("bag_id"), "reason": "缺少 start_ts/end_ts"})
            continue
        tag = f"{strategy_name}_{'positive' if c.get('verdict') == 'pass' else 'negative'}"
        label_res_list.append({
            "bin_id": c["bag_id"],
            "mining_table": settings.DM_PROD_TABLE,
            "tag_name": tag,
            "start_ts": int(c["start_ts"]) * 10**9,
            "end_ts": int(c["end_ts"]) * 10**9,
            "version": "v1",
        })

    if not label_res_list:
        raise HTTPException(status_code=400, detail="全部标注缺少时间戳，无法同步")

    try:
        data = await upload_evalset(req.benchmark_name, label_res_list)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("sync-evalset failed")
        raise HTTPException(status_code=502, detail=f"同步异常: {e}")

    return {
        "ok": True,
        "benchmark_name": req.benchmark_name,
        "submitted": len(label_res_list),
        "skipped": skipped,
        "result": data,
    }
