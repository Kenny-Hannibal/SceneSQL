# -*- coding: utf-8 -*-
"""Fusion Map BEV 视图 API

提供 EFusionMap 数据接口，供前端 Three.js BEV 渲染使用。
"""
import logging
from fastapi import APIRouter, Query
from app.services.fusion_map_parser import get_fusion_map_info, read_fusion_map_frame, read_fusion_map_frames_range

router = APIRouter(prefix="/api/bag", tags=["fusion-map"])
logger = logging.getLogger(__name__)


@router.get("/fusion-map-info")
def fusion_map_info(bag_path: str = Query(..., description="bag 目录路径")):
    """获取 fusion_map_plus bin 文件基本信息（帧数、文件大小等）"""
    return get_fusion_map_info(bag_path)


@router.get("/fusion-map-frame")
def fusion_map_frame(
    bag_path: str = Query(..., description="bag 目录路径"),
    frame_idx: int = Query(0, description="帧索引（0-based）"),
):
    """读取指定帧的 EFusionMap 解码数据"""
    return read_fusion_map_frame(bag_path, frame_idx)


@router.get("/fusion-map-frames-range")
def fusion_map_frames_range(
    bag_path: str = Query(..., description="bag 目录路径"),
    start: int = Query(0, description="起始帧索引（inclusive）"),
    end: int = Query(10, description="结束帧索引（exclusive）"),
):
    """批量读取帧数据 [start, end)，用于前端预加载/动画播放

    为避免响应过大，单次最多返回 200 帧。
    """
    end = min(end, start + 200)
    return read_fusion_map_frames_range(bag_path, start, end)
