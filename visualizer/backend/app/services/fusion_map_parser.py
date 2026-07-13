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


def _decode_efusionmap(pb_bytes: bytes) -> Optional[Dict]:
    """解码 EFusionMap protobuf 为简化的可视化 JSON"""
    decoders = _init_pb_decoders()
    msg_type = decoders.get('/gac/enviro_model/fusion_map_plus')
    if msg_type is None:
        return None
    try:
        msg = msg_type()
        msg.ParseFromString(pb_bytes)
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


def _get_offsets(bin_path: str) -> List[int]:
    """扫描 PB01 bin 文件，构建帧偏移索引（只做一次）"""
    if bin_path in _offset_cache:
        return _offset_cache[bin_path]
    offsets = []
    with open(bin_path, 'rb') as f:
        header = f.read(8)
        if len(header) < 8 or header[:4] != b'PB01':
            _offset_cache[bin_path] = offsets
            return offsets
        count = struct.unpack('<I', header[4:8])[0]
        f.seek(8)
        for _ in range(count):
            offset = f.tell()
            offsets.append(offset)
            frame_header = f.read(24)
            if len(frame_header) < 24:
                break
            _, _, _, length = struct.unpack('<IddI', frame_header)
            f.seek(length, 1)
    _offset_cache[bin_path] = offsets
    logger.info("FusionMap帧索引已构建: %d帧, %s", len(offsets), bin_path)
    return offsets


def get_fusion_map_info(bag_path: str) -> Dict:
    """获取本地 fusion_map_plus bin 文件基本信息"""
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

        return {
            'exists': True,
            'format': 'PB01' if magic == b'PB01' else 'unknown',
            'total_frames': count,
            'file_size_mb': round(file_size / 1024 / 1024, 1),
            'bin_path': bin_path,
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
