# -*- coding: utf-8 -*-
"""Fusion Map (EFusionMap) 解析器

从 PB01 bin 文件中读取 /gac/enviro_model/fusion_map_plus 数据，
解码 EFusionMap protobuf，返回可视化 JSON。

借鉴 UBM_Data_IDE/backend/vis_server.py 的 _decode_efusionmap / _read_fusion_map_frame / _get_fusion_map_info。
"""
import os
import struct
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# ── Protobuf 解码器（延迟加载）──
_pb_decoders = None


def _init_pb_decoders():
    """初始化 protobuf 解码器（延迟加载）

    EnviroModeling.boleidl_pb2 引用了 gac.Comm 中的 RoutingInfo、AlphaSdInfo 等类型。
    SceneSQL 的 scripts/proto/j6/Comm/boleidl_pb2.py 已替换为 UBM 的完整版本（超集），
    因此直接从本地 proto 路径加载即可，无需额外搜索 UBM 路径。
    """
    global _pb_decoders
    if _pb_decoders is not None:
        return _pb_decoders
    try:
        import sys as _sys
        from app.core.config import settings
        project_root = str(settings.PROJECT_ROOT)
        proto_dir = os.path.join(project_root, "scripts/proto")
        j6_dir = os.path.join(project_root, "scripts/proto/j6")
        for p in [proto_dir, j6_dir]:
            if p not in _sys.path:
                _sys.path.insert(0, p)
        from j6.EnviroModeling import boleidl_pb2 as j6_EnviroModeling
        _pb_decoders = {
            '/gac/enviro_model/fusion_map_plus': j6_EnviroModeling.EFusionMap,
        }
        logger.info("FusionMap protobuf 解码器已加载")
        return _pb_decoders
    except Exception as e:
        logger.warning("FusionMap protobuf decoder 加载失败: %s", e)
        _pb_decoders = {}
        return _pb_decoders


def _strip_heavy_fields(pb_bytes: bytes) -> bytes:
    """从 protobuf raw bytes 中移除 BEV 渲染不需要的大字段，加速 ParseFromString。

    跳过 field#21 (pre_decider_data, ~371KB) 和 field#22 (occupancy, ~184KB)，
    这两个字段占帧数据 80% 体积但 BEV 可视化完全不需要。
    裁剪后 protobuf 解码从 ~170ms/帧 降到 ~20ms/帧 (8x 加速)。
    """
    # 需要跳过的 field number
    SKIP_FIELDS = {21, 22}
    result = bytearray()
    pos = 0
    n = len(pb_bytes)

    while pos < n:
        tag_start = pos
        # 读 tag (varint)
        tag = 0
        shift = 0
        while pos < n:
            b = pb_bytes[pos]
            tag |= (b & 0x7F) << shift
            pos += 1
            shift += 7
            if not (b & 0x80):
                break

        field_num = tag >> 3
        wire_type = tag & 0x7

        # 跳过该字段的 value
        if wire_type == 0:  # varint
            while pos < n and (pb_bytes[pos] & 0x80):
                pos += 1
            pos += 1
        elif wire_type == 1:  # 64-bit fixed
            pos += 8
        elif wire_type == 2:  # length-delimited
            length = 0
            lshift = 0
            while pos < n:
                b = pb_bytes[pos]
                length |= (b & 0x7F) << lshift
                pos += 1
                lshift += 7
                if not (b & 0x80):
                    break
            pos += length
        elif wire_type == 5:  # 32-bit fixed
            pos += 4
        else:
            # 未知 wire type，无法安全跳过，返回原始数据
            return pb_bytes

        # 只保留非跳过字段
        if field_num not in SKIP_FIELDS:
            result.extend(pb_bytes[tag_start:pos])

    return bytes(result)


def _decode_efusionmap(pb_bytes: bytes) -> Optional[Dict]:
    """解码 EFusionMap protobuf 为简化的可视化 JSON

    优化：先裁剪掉 pre_decider_data 和 occupancy 等大字段，
    再 ParseFromString，解码速度提升 8 倍。
    """
    decoders = _init_pb_decoders()
    msg_type = decoders.get('/gac/enviro_model/fusion_map_plus')
    if msg_type is None:
        return None
    try:
        # 先裁剪大字段，再解码
        stripped = _strip_heavy_fields(pb_bytes)
        msg = msg_type()
        msg.ParseFromString(stripped)
        result = {
            'timestamp_ns': int(msg.timestamp.sec * 1e9 + msg.timestamp.nsec),
            'veh_loc': None,
            'obstacles': [],
            'paths': [],
            'boundaries': [],
            'lanes': [],
        }
        # 车辆位置
        try:
            if msg.WhichOneof('_veh_loc') is not None:
                vl = msg.veh_loc
                if vl.HasField('local_pose'):
                    lp = vl.local_pose
                    result['veh_loc'] = {
                        'x': round(lp.translation.x, 4),
                        'y': round(lp.translation.y, 4),
                        'z': round(lp.translation.z, 4),
                        'qw': round(lp.rotation.w, 6),
                        'qx': round(lp.rotation.x, 6),
                        'qy': round(lp.rotation.y, 6),
                        'qz': round(lp.rotation.z, 6),
                    }
        except Exception:
            pass

        # 障碍物
        for obs in msg.obstacles_vector:
            try:
                obs_data = {
                    'id': obs.obstacle_id,
                    'type': obs.obstacle_type_info.type if obs.HasField('obstacle_type_info') else 0,
                    'x': round(obs.center_position.x, 3),
                    'y': round(obs.center_position.y, 3),
                    'w': round(obs.bbox3d_width, 3),
                    'h': round(obs.bbox3d_height, 3),
                    'l': round(obs.bbox3d_length, 3),
                    'heading': round(obs.heading_angle, 5),
                    'movable': obs.is_movable,
                }
                result['obstacles'].append(obs_data)
            except Exception:
                pass

        # 路径
        try:
            if msg.WhichOneof('_routingpaths_vector') is not None:
                for path in msg.routingpaths_vector.paths:
                    pts = [[round(p.x, 3), round(p.y, 3)] for p in path.points]
                    if pts:
                        result['paths'].append({'points': pts})
                for mp in msg.routingpaths_vector.model_paths:
                    pts = [[round(p.x, 3), round(p.y, 3)] for p in mp.points]
                    if pts:
                        result['paths'].append({'points': pts, 'type': 'model'})
        except Exception:
            pass

        # 边界线
        for bnd in msg.boundary_vector:
            try:
                pts = []
                for p in bnd.center_line:
                    try:
                        if hasattr(p, 'point'):
                            pts.append([round(p.point.x, 3), round(p.point.y, 3)])
                        else:
                            pts.append([round(p.x, 3), round(p.y, 3), round(getattr(p, 'z', 0), 3)])
                    except Exception:
                        pass
                if pts:
                    result['boundaries'].append({'id': bnd.boundary_id, 'points': pts})
            except Exception:
                pass

        # 车道
        for lane in msg.lane_vector:
            try:
                pts = []
                for p in lane.center_line:
                    try:
                        if hasattr(p, 'point'):
                            pts.append([round(p.point.x, 3), round(p.point.y, 3)])
                        else:
                            pts.append([round(p.x, 3), round(p.y, 3), round(getattr(p, 'z', 0), 3)])
                    except Exception:
                        pass
                if pts:
                    result['lanes'].append({'id': lane.lane_id, 'type': lane.lane_type, 'points': pts})
            except Exception:
                pass

        # fusion_map_rule 中的车道/边界/stopline
        try:
            if msg.WhichOneof('_fusion_map_rule') is not None:
                fmr = msg.fusion_map_rule
                for lane in fmr.lane_vector:
                    try:
                        pts = []
                        for p in lane.center_line:
                            try:
                                if hasattr(p, 'point'):
                                    pts.append([round(p.point.x, 3), round(p.point.y, 3)])
                                else:
                                    pts.append([round(p.x, 3), round(p.y, 3), round(getattr(p, 'z', 0), 3)])
                            except Exception:
                                pass
                        if pts:
                            result['lanes'].append({'id': lane.lane_id, 'type': lane.lane_type, 'points': pts, 'source': 'rule'})
                    except Exception:
                        pass
                for bnd in fmr.boundary_vector:
                    try:
                        pts = []
                        for p in bnd.center_line:
                            try:
                                if hasattr(p, 'point'):
                                    pts.append([round(p.point.x, 3), round(p.point.y, 3)])
                                else:
                                    pts.append([round(p.x, 3), round(p.y, 3), round(getattr(p, 'z', 0), 3)])
                            except Exception:
                                pass
                        if pts:
                            result['boundaries'].append({'id': bnd.boundary_id, 'points': pts, 'source': 'rule'})
                    except Exception:
                        pass
                # stopline
                for sl in fmr.stopline_vector:
                    try:
                        pts = []
                        for p in sl.points:
                            try:
                                if hasattr(p, 'point'):
                                    pts.append([round(p.point.x, 3), round(p.point.y, 3)])
                                else:
                                    pts.append([round(p.x, 3), round(p.y, 3)])
                            except Exception:
                                pass
                        if pts:
                            result['boundaries'].append({'id': getattr(sl, 'stopline_id', 0), 'points': pts, 'source': 'stopline'})
                    except Exception:
                        pass
        except Exception:
            pass

        return result
    except Exception as e:
        logger.debug("EFusionMap解码失败: %s", e)
        return None


# ── PB01 bin 文件帧偏移缓存 ──
_offset_cache: Dict[str, List[int]] = {}
# ── 帧时间戳缓存（protobuf 内的 timestamp_ns，纳秒级整数）──
_ts_ns_cache: Dict[str, List[int]] = {}


def _get_offsets(bin_path: str) -> List[int]:
    """扫描 PB01 bin 文件，构建帧偏移索引（只做一次）"""
    if bin_path in _offset_cache:
        return _offset_cache[bin_path]
    offsets = []
    with open(bin_path, 'rb') as f:
        header = f.read(8)
        if len(header) < 8 or header[:4] != b'PB01':
            _offset_cache[bin_path] = offsets
            _ts_ns_cache[bin_path] = []
            return offsets
        count = struct.unpack('<I', header[4:8])[0]
        f.seek(8)
        for _ in range(count):
            offset = f.tell()
            frame_header = f.read(24)
            if len(frame_header) < 24:
                break
            _, _, _, length = struct.unpack('<IddI', frame_header)
            offsets.append(offset)
            f.seek(length, 1)
    _offset_cache[bin_path] = offsets
    _ts_ns_cache[bin_path] = []  # 延迟构建
    logger.info("FusionMap帧索引已构建: %d帧, %s", len(offsets), bin_path)
    return offsets


def _get_ts_ns_index(bin_path: str) -> List[int]:
    """构建/获取帧的 timestamp_ns 索引（纳秒整数列表，与 offset 一一对应）

    PB01 帧头的 pub_ts 不可靠（某些 bag 里是极小值），
    所以从 protobuf payload 中解码 timestamp.sec + timestamp.nsec。
    为提高性能，一次性顺序读取整个文件，逐帧提取 timestamp。
    """
    if bin_path in _ts_ns_cache and _ts_ns_cache[bin_path]:
        return _ts_ns_cache[bin_path]

    offsets = _get_offsets(bin_path)
    if not offsets:
        _ts_ns_cache[bin_path] = []
        return []

    timestamps = []
    # ── 优化：partial parse 只提取 timestamp，不完整解码 protobuf ──
    # EFusionMap.timestamp 是 field 1 (tag 0x0a, length-delimited)
    # Comm.TimeStamp 内部: sec = field 1 (tag 0x08, varint), nsec = field 2 (tag 0x10, varint)
    # 性能：1185帧 0.07s vs 完整protobuf解码 263s (3700x 加速)
    with open(bin_path, 'rb') as f:
        for idx, offset in enumerate(offsets):
            try:
                f.seek(offset)
                fh = f.read(24)
                if len(fh) < 24:
                    timestamps.append(0)
                    continue
                _, _, _, length = struct.unpack('<IddI', fh)
                payload = f.read(length)
                if len(payload) < length:
                    timestamps.append(0)
                    continue

                ts_ns = _extract_timestamp_partial(payload)
                timestamps.append(ts_ns if ts_ns is not None else 0)
            except Exception as e:
                logger.debug("帧%d时间戳partial parse失败: %s", idx, e)
                timestamps.append(0)

    _ts_ns_cache[bin_path] = timestamps
    logger.info("FusionMap时间戳索引已构建(partial parse): %d帧, ts范围[%d, %d], 耗时<1s",
                len(timestamps), timestamps[0] if timestamps else 0,
                timestamps[-1] if timestamps else 0)
    return timestamps


def _extract_timestamp_partial(payload: bytes):
    """从 EFusionMap protobuf payload 中快速提取 timestamp (ns)

    只解析前几个字节，不构建完整 protobuf 对象。
    EFusionMap.timestamp = field 1 (tag=0x0a, length-delimited) → Comm.TimeStamp
    TimeStamp.sec = field 1 (tag=0x08, varint)
    TimeStamp.nsec = field 2 (tag=0x10, varint)

    Returns:
        int (纳秒) 或 None
    """
    pos = 0
    if pos >= len(payload) or payload[pos] != 0x0a:
        return None
    pos += 1

    # 读取 TimeStamp message 长度 (varint)
    ts_len = 0
    shift = 0
    while pos < len(payload):
        b = payload[pos]
        ts_len |= (b & 0x7f) << shift
        pos += 1
        shift += 7
        if not (b & 0x80):
            break
    ts_start = pos
    sec = 0
    nsec = 0
    while pos < ts_start + ts_len:
        tag = payload[pos]
        pos += 1
        if tag == 0x08:  # sec
            val = 0
            sh = 0
            while pos < len(payload):
                b = payload[pos]
                val |= (b & 0x7f) << sh
                pos += 1
                sh += 7
                if not (b & 0x80):
                    break
            sec = val
        elif tag == 0x10:  # nsec
            val = 0
            sh = 0
            while pos < len(payload):
                b = payload[pos]
                val |= (b & 0x7f) << sh
                pos += 1
                sh += 7
                if not (b & 0x80):
                    break
            nsec = val
        else:
            # 跳过未知字段
            wt = tag & 0x07
            if wt == 0:  # varint
                while pos < len(payload) and payload[pos] & 0x80:
                    pos += 1
                pos += 1
            elif wt == 2:  # length-delimited
                ln = 0
                sh = 0
                while pos < len(payload):
                    b = payload[pos]
                    ln |= (b & 0x7f) << sh
                    pos += 1
                    sh += 7
                    if not (b & 0x80):
                        break
                pos += ln
            else:
                pos += 1  # fixed32/fixed64 简化处理

    return sec * 1_000_000_000 + nsec


def find_frame_idx_by_ts(bag_path: str, ts_ns: int) -> Dict:
    """根据纳秒时间戳找到最近的帧索引（二分查找）

    Args:
        bag_path: bag 目录路径
        ts_ns: 目标时间戳（纳秒）

    Returns:
        {'frame_idx': int, 'ts_ns': int, 'actual_ts_ns': int, 'total_frames': int}
    """
    import bisect
    bin_path = os.path.join(bag_path, 'bin', 'gac_enviro_model_fusion_map_plus.bin')
    if not os.path.exists(bin_path):
        return {'error': '文件不存在', 'frame_idx': 0}

    offsets = _get_offsets(bin_path)
    timestamps = _get_ts_ns_index(bin_path)
    total = len(offsets)
    if total == 0 or not timestamps:
        return {'error': '无帧数据', 'frame_idx': 0, 'total_frames': 0}

    # 二分查找：找到 timestamp_ns <= ts_ns 的最大帧
    idx = bisect.bisect_right(timestamps, ts_ns) - 1
    idx = max(0, min(idx, total - 1))

    return {
        'frame_idx': idx,
        'ts_ns': ts_ns,
        'actual_ts_ns': timestamps[idx],
        'total_frames': total,
    }


def get_fusion_map_info(bag_path: str) -> Dict:
    """获取本地 fusion_map_plus bin 文件基本信息

    同时预构建帧偏移索引和时间戳索引，避免后续 find_frame_idx_by_ts
    首次调用时逐帧解码 timestamp 导致 4 分钟卡顿。
    """
    bin_path = os.path.join(bag_path, 'bin', 'gac_enviro_model_fusion_map_plus.bin')
    if not os.path.exists(bin_path):
        return {'exists': False, 'error': '文件不存在: {}'.format(bin_path)}

    try:
        file_size = os.path.getsize(bin_path)
        with open(bin_path, 'rb') as f:
            header = f.read(8)
            if len(header) < 8:
                return {'exists': True, 'error': '文件太小', 'total_frames': 0}
            magic = header[:4]
            count = struct.unpack('<I', header[4:8])[0] if magic == b'PB01' else 0

        # 预构建帧偏移和时间戳索引（首次耗时，后续命中缓存）
        if count > 0:
            _get_offsets(bin_path)
            _get_ts_ns_index(bin_path)

        # 获取首帧时间戳（用于前端显示相对时间）
        ts_index = _get_ts_ns_index(bin_path) if count > 0 else []
        first_ts_ns = ts_index[0] if ts_index else 0

        return {
            'exists': True,
            'format': 'PB01' if magic == b'PB01' else 'unknown',
            'total_frames': count,
            'file_size_mb': round(file_size / 1024 / 1024, 1),
            'bin_path': bin_path,
            'first_ts_ns': first_ts_ns,
        }
    except Exception as e:
        return {'exists': False, 'error': str(e)}


def read_fusion_map_frame(bag_path: str, frame_idx: int) -> Dict:
    """从本地 PB01 bin 文件读取 fusion_map_plus 指定帧并解码

    使用帧偏移缓存加速：首次扫描文件构建索引，后续直接 seek
    """
    bin_path = os.path.join(bag_path, 'bin', 'gac_enviro_model_fusion_map_plus.bin')
    if not os.path.exists(bin_path):
        return {'error': '文件不存在: {}'.format(bin_path)}

    try:
        offsets = _get_offsets(bin_path)
        with open(bin_path, 'rb') as f:
            header = f.read(8)
            if len(header) < 8 or header[:4] != b'PB01':
                return {'error': '非PB01格式'}
            count = struct.unpack('<I', header[4:8])[0]

            if frame_idx < 0 or frame_idx >= count:
                return {'error': '帧索引越界: {}, 总帧数={}'.format(frame_idx, count)}

            if frame_idx >= len(offsets):
                return {'error': '帧偏移索引不完整, idx={}, 缓存长度={}'.format(frame_idx, len(offsets))}

            f.seek(offsets[frame_idx])
            frame_header = f.read(24)
            if len(frame_header) < 24:
                return {'error': '读取帧头失败, idx={}'.format(frame_idx)}
            seq_num, pub_ts, recv_ts, length = struct.unpack('<IddI', frame_header)
            data = f.read(length)
            if len(data) < length:
                return {'error': '读取帧数据失败, idx={}, 期望{}字节'.format(frame_idx, length)}

            frame_info = {'seq_num': seq_num, 'pub_ts': pub_ts, 'recv_ts': recv_ts, 'length': length}

            decoded = _decode_efusionmap(data)
            if decoded is None:
                return {'error': 'protobuf解码失败', 'frame_info': frame_info}

            return {'frame_idx': frame_idx, 'total_frames': count, 'frame_info': frame_info, 'data': decoded}

    except Exception as e:
        return {'error': str(e)}


def read_fusion_map_frames_range(bag_path: str, start: int, end: int) -> Dict:
    """批量读取 fusion_map_plus 帧 [start, end)，用于前端预加载/动画播放

    返回 {'frames': [...], 'total_frames': N}
    每个元素是 read_fusion_map_frame 的返回值（去掉了 frame_info 以减小体积）
    """
    bin_path = os.path.join(bag_path, 'bin', 'gac_enviro_model_fusion_map_plus.bin')
    if not os.path.exists(bin_path):
        return {'error': '文件不存在', 'frames': []}

    try:
        offsets = _get_offsets(bin_path)
        info = get_fusion_map_info(bag_path)
        total = info.get('total_frames', 0)
        if total == 0:
            return {'error': '无法读取文件头', 'frames': [], 'total_frames': 0}

        end = min(end, total)
        frames = []

        with open(bin_path, 'rb') as f:
            for idx in range(start, end):
                if idx >= len(offsets):
                    break
                try:
                    f.seek(offsets[idx])
                    frame_header = f.read(24)
                    if len(frame_header) < 24:
                        continue
                    seq_num, pub_ts, recv_ts, length = struct.unpack('<IddI', frame_header)
                    data = f.read(length)
                    if len(data) < length:
                        continue
                    decoded = _decode_efusionmap(data)
                    if decoded is not None:
                        frames.append({'frame_idx': idx, 'data': decoded})
                except Exception:
                    continue

        return {'frames': frames, 'total_frames': total}

    except Exception as e:
        return {'error': str(e), 'frames': []}
