import logging
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.models.schemas import BagInfo
from app.services.bag_parser import get_bag_info
from app.core.exceptions import BagNotFoundException

router = APIRouter(prefix="/api/bag", tags=["bag"])
logger = logging.getLogger(__name__)


@router.post("/info", response_model=BagInfo)
def bag_info(bag_path: str):
    logger.info("Loading bag info: %s", bag_path)
    info = get_bag_info(bag_path)
    return info


@router.post("/info-stream")
async def bag_info_stream(bag_path: str):
    """SSE streaming endpoint for bag parsing progress."""
    import asyncio

    async def event_generator():
        yield f"data: {json.dumps({'stage': 'loading', 'message': '正在加载 bag 信息...'})}\n\n"
        await asyncio.sleep(0.1)

        try:
            yield f"data: {json.dumps({'stage': 'parsing_topics', 'message': '正在解析 bag topics...'})}\n\n"
            info = await asyncio.get_event_loop().run_in_executor(None, get_bag_info, bag_path)
            yield f"data: {json.dumps({'stage': 'completed', 'bag_info': info.dict()})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'stage': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
