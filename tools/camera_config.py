"""
从 bag 路径读取摄像头超参信息，动态匹配摄像头属性
"""
import os
import json
from typing import Dict, List, Tuple, Optional

# 摄像头名称到 (cam_id, topic, display_name) 的映射
CAMERA_NAME_MAP = {
    'center_camera_fov120': ('fw120', '/gac/cam/fw120_encoded', '前视120°'),
    'center_camera_fov60': ('fw60', '/gac/cam/fw60_encoded', '前视60°'),
    'center_camera_fov30': ('ft30', '/gac/cam/ft30_encoded', '前视30°'),
    'center_camera_fov20': ('ft20', '/gac/cam/ft20_encoded', '前视20°'),
    'rear_camera': ('r50', '/gac/cam/r50_encoded', '后视50°'),
    'left_front_camera': ('fl99', '/gac/cam/fl99_encoded', '左前99°'),
    'right_front_camera': ('fr99', '/gac/cam/fr99_encoded', '右前99°'),
    'left_rear_camera': ('rl99', '/gac/cam/rl99_encoded', '左后99°'),
    'right_rear_camera': ('rr99', '/gac/cam/rr99_encoded', '右后99°'),
}


def load_camera_config(bag_path: str) -> Dict[str, dict]:
    """
    从 bag 路径的 camera/virtual/ 目录读取摄像头超参信息
    返回: {cam_id: {'name': str, 'topic': str, 'display_name': str, 'intrinsic': dict, 'extrinsic': dict}}
    """
    config = {}
    virtual_dir = os.path.join(bag_path, 'camera', 'virtual')
    if not os.path.isdir(virtual_dir):
        return config
    
    for cam_name in sorted(os.listdir(virtual_dir)):
        cam_dir = os.path.join(virtual_dir, cam_name)
        if not os.path.isdir(cam_dir):
            continue
        
        if cam_name not in CAMERA_NAME_MAP:
            continue
        
        cam_id, topic, display_name = CAMERA_NAME_MAP[cam_name]
        
        # 读取内参
        intrinsic_path = os.path.join(cam_dir, f'{cam_name}-intrinsic.json')
        intrinsic = None
        if os.path.exists(intrinsic_path):
            try:
                with open(intrinsic_path, 'r') as f:
                    intrinsic = json.load(f)
            except Exception as e:
                print(f"[WARNING] 读取内参失败 {intrinsic_path}: {e}")
        
        # 读取外参
        extrinsic_path = os.path.join(cam_dir, f'{cam_name}-to-car_center-extrinsic.json')
        extrinsic = None
        if os.path.exists(extrinsic_path):
            try:
                with open(extrinsic_path, 'r') as f:
                    extrinsic = json.load(f)
            except Exception as e:
                print(f"[WARNING] 读取外参失败 {extrinsic_path}: {e}")
        
        config[cam_id] = {
            'name': cam_name,
            'topic': topic,
            'display_name': display_name,
            'intrinsic': intrinsic,
            'extrinsic': extrinsic,
        }
    
    return config


def build_camera_layout(cameras: Dict[str, dict]) -> List[List[Tuple[str, str]]]:
    """
    根据摄像头数量动态构建布局
    返回: [[(cam_id, display_name), ...], ...]
    """
    items = [(cam_id, info['display_name']) for cam_id, info in cameras.items()]
    
    if not items:
        return []
    
    n = len(items)
    # 尽量排成接近正方形的网格
    cols = int(n ** 0.5)
    if cols * cols < n:
        cols += 1
    if cols * (cols - 1) >= n and cols > 1:
        cols -= 1
    
    layout = []
    for i in range(0, n, cols):
        layout.append(items[i:i+cols])
    
    return layout
