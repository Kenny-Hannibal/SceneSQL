# -*- coding: utf-8 -*-
"""Fusion Map BEV 视图 API

提供 EFusionMap 数据接口，供前端 Three.js BEV 渲染使用。
"""
import logging
from fastapi import APIRouter, Query
from app.services.fusion_map_parser import (
    get_fusion_map_info, read_fusion_map_frame, read_fusion_map_frames_range,
    find_frame_idx_by_ts,
)

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


@router.get("/fusion-map-frame-by-ts")
def fusion_map_frame_by_ts(
    bag_path: str = Query(..., description="bag 目录路径"),
    ts_ns: int = Query(..., description="目标时间戳（纳秒）"),
):
    """根据时间戳查找最近的帧，返回帧索引信息

    前端拿到 frame_idx 后，再调用 /fusion-map-frame 读取实际帧数据。
    也可同时传 ts_ns，让前端知道实际帧的时间戳与目标时间的偏差。
    """
    return find_frame_idx_by_ts(bag_path, ts_ns)


@router.get("/fusion-map-frames-by-ts-range")
def fusion_map_frames_by_ts_range(
    bag_path: str = Query(..., description="bag 目录路径"),
    start_ts_ns: int = Query(..., description="起始时间戳（纳秒）"),
    end_ts_ns: int = Query(None, description="结束时间戳（纳秒），不传则返回起始帧索引"),
):
    """一次性查询 start_ts_ns 和 end_ts_ns 对应的帧索引

    比 /fusion-map-frame-by-ts 调两次更高效：ts 索引只构建一次。
    返回 {'start_frame_idx': int, 'end_frame_idx': int|null, 'total_frames': int}
    """
    import bisect
    from app.services.fusion_map_parser import _get_offsets, _get_ts_ns_index
    import os

    bin_path = os.path.join(bag_path, 'bin', 'gac_enviro_model_fusion_map_plus.bin')
    if not os.path.exists(bin_path):
        return {'error': '文件不存在', 'start_frame_idx': 0}

    offsets = _get_offsets(bin_path)
    timestamps = _get_ts_ns_index(bin_path)
    total = len(offsets)
    if total == 0 or not timestamps:
        return {'error': '无帧数据', 'start_frame_idx': 0, 'total_frames': 0}

    # start
    s_idx = bisect.bisect_right(timestamps, start_ts_ns) - 1
    s_idx = max(0, min(s_idx, total - 1))

    result = {
        'start_frame_idx': s_idx,
        'start_actual_ts_ns': timestamps[s_idx],
        'total_frames': total,
    }

    # end
    if end_ts_ns is not None:
        e_idx = bisect.bisect_right(timestamps, end_ts_ns) - 1
        e_idx = max(0, min(e_idx, total - 1))
        result['end_frame_idx'] = e_idx
        result['end_actual_ts_ns'] = timestamps[e_idx]

    return result
