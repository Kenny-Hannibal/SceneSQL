"""Mage-VL 评测 API — 把 bag 视频片段提取为 mp4 后送入 Mage-VL 服务评测。

流程：
  1. 接收 bag_id + start_ts + end_ts（+ 可选 topic / prompt）
  2. 解析 bag 本地路径（dm_sdk / 本地 / OSS 下载）
  3. 调用 extract_topic_to_mp4 提取 H.264 mp4 到临时文件
  4. 读取 mp4 bytes → base64 编码
  5. POST 到 Mage-VL 服务 (http://localhost:31000/v1/chat/completions)
  6. 返回评测结果

Mage-VL 服务端口 31000，OpenAI-compatible API。
"""
import os
import base64
import logging
import httpx
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.video_extractor import extract_topic_to_mp4, get_task
from app.services.frame_extractor import (
    _resolve_bag_path_via_dm,
    _resolve_bag_path_local,
    _download_bag_from_oss,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mage-vl", tags=["mage-vl"])

# ── 配置 ──
MAGE_VL_BASE_URL = os.environ.get("MAGE_VL_BASE_URL", "http://localhost:31000")
MAGE_VL_TIMEOUT = float(os.environ.get("MAGE_VL_TIMEOUT", "120"))
DEFAULT_CAMERA_TOPIC = "/gac/cam/orig_fw120_encoded"
DEFAULT_PROMPT = "请描述这段自动驾驶视频中的场景，包括自车行为、周围对象、是否有冲突或加塞等情况。"


# ── 请求 / 响应模型 ──

class EvalRequest(BaseModel):
    bag_id: str
    start_ts: Optional[int] = None  # 纳秒
    end_ts: Optional[int] = None    # 纳秒
    topic: Optional[str] = None     # 默认 /gac/cam/orig_fw120_encoded (前视宽120°)
    prompt: Optional[str] = None    # 默认场景描述 prompt
    max_tokens: Optional[int] = 512


class EvalResponse(BaseModel):
    ok: bool
    bag_id: str
    evaluation: str = ""
    error: str = ""


# ── 端点 ──

@router.post("/evaluate", response_model=EvalResponse)
async def evaluate_video(req: EvalRequest):
    """提取 bag 视频片段 → 送入 Mage-VL 评测 → 返回自然语言评价。"""
    import uuid
    import asyncio

    task_id = f"magevl_{uuid.uuid4().hex[:8]}"
    topic = req.topic or DEFAULT_CAMERA_TOPIC
    prompt = req.prompt or DEFAULT_PROMPT

    logger.info("[%s] Mage-VL eval: bag_id=%s topic=%s range=[%s, %s]",
                task_id, req.bag_id, topic, req.start_ts, req.end_ts)

    # ── 1. 解析 bag 路径 ──
    bag_path = None
    oss_path = None

    try:
        dm_result = _resolve_bag_path_via_dm(req.bag_id)
        bag_path = dm_result.local_path
        oss_path = dm_result.oss_path
    except Exception:
        pass

    if not bag_path:
        local_result = _resolve_bag_path_local(req.bag_id)
        bag_path = local_result.local_path
        if not oss_path:
            oss_path = local_result.oss_path

    if not bag_path and oss_path:
        try:
            bag_path = _download_bag_from_oss(oss_path, f"{task_id}/bag", req.bag_id)
        except Exception as exc:
            logger.warning("[%s] OSS download failed: %s", task_id, exc)

    if not bag_path:
        return EvalResponse(ok=False, bag_id=req.bag_id,
                            error=f"Cannot resolve bag_id={req.bag_id}")

    # ── 2. 提取视频片段 → mp4 临时文件 ──
    mp4_path = os.path.join(os.environ.get("VIDEO_OUTPUT_DIR", "/tmp/video_output"),
                            f"{task_id}.mp4")

    # extract_topic_to_mp4 是同步阻塞函数，放到线程池执行
    loop = asyncio.get_event_loop()
    try:
        result_path = await loop.run_in_executor(
            None,
            lambda: extract_topic_to_mp4(
                bag_path=bag_path,
                topic=topic,
                task_id=task_id,
                start_ts=req.start_ts,
                end_ts=req.end_ts,
            )
        )
        if result_path:
            mp4_path = result_path
        else:
            # 检查任务状态
            task = get_task(task_id)
            msg = task.get("message", "Unknown error") if task else "No task found"
            return EvalResponse(ok=False, bag_id=req.bag_id, error=f"Extraction failed: {msg}")
    except Exception as exc:
        logger.exception("[%s] Extraction exception", task_id)
        return EvalResponse(ok=False, bag_id=req.bag_id, error=f"Extraction exception: {exc}")

    if not os.path.exists(mp4_path):
        return EvalResponse(ok=False, bag_id=req.bag_id,
                            error=f"MP4 file not found: {mp4_path}")

    # ── 3. 读取 mp4 → base64 ──
    try:
        with open(mp4_path, "rb") as f:
            video_bytes = f.read()
        video_b64 = base64.b64encode(video_bytes).decode("utf-8")
        logger.info("[%s] MP4 size=%.1fMB, base64 length=%d",
                    task_id, len(video_bytes) / 1024 / 1024, len(video_b64))
    except Exception as exc:
        logger.exception("[%s] Failed to read mp4", task_id)
        return EvalResponse(ok=False, bag_id=req.bag_id, error=f"Read mp4 failed: {exc}")

    # ── 4. POST 到 Mage-VL ──
    data_url = f"data:video/mp4;base64,{video_b64}"
    payload = {
        "model": "mage-vl",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": req.max_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=MAGE_VL_TIMEOUT) as client:
            resp = await client.post(
                f"{MAGE_VL_BASE_URL}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except httpx.ConnectError:
        return EvalResponse(ok=False, bag_id=req.bag_id,
                            error=f"Cannot connect to Mage-VL at {MAGE_VL_BASE_URL}")
    except httpx.TimeoutException:
        return EvalResponse(ok=False, bag_id=req.bag_id,
                            error=f"Mage-VL timeout ({MAGE_VL_TIMEOUT}s)")
    except Exception as exc:
        logger.exception("[%s] Mage-VL request failed", task_id)
        return EvalResponse(ok=False, bag_id=req.bag_id, error=f"Request failed: {exc}")

    if resp.status_code != 200:
        return EvalResponse(ok=False, bag_id=req.bag_id,
                            error=f"Mage-VL HTTP {resp.status_code}: {resp.text[:500]}")

    # ── 5. 解析结果 ──
    try:
        data = resp.json()
        evaluation = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        logger.exception("[%s] Failed to parse Mage-VL response", task_id)
        return EvalResponse(ok=False, bag_id=req.bag_id, error=f"Parse failed: {exc}")

    # ── 6. 清理临时 mp4 ──
    try:
        os.remove(mp4_path)
    except Exception:
        pass

    return EvalResponse(ok=True, bag_id=req.bag_id, evaluation=evaluation)


@router.get("/health")
async def mage_vl_health():
    """检查 Mage-VL 服务是否在线。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{MAGE_VL_BASE_URL}/health")
            return {"ok": resp.status_code == 200, "url": MAGE_VL_BASE_URL}
    except Exception:
        return {"ok": False, "url": MAGE_VL_BASE_URL}
