import logging
from fastapi import APIRouter
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
