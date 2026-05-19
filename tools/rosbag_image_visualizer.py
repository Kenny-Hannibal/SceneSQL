#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rosbag Image Visualizer V2 - 多视角视频预览版
支持多路相机同步播放、视频导出、播放控制
动态适配 bag 中的摄像头配置
"""

import os
import sys
import glob
import tempfile
import shutil
from typing import List, Tuple, Optional, Dict
from collections import defaultdict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import io

import gradio as gr
import numpy as np
from PIL import Image
import cv2

# ---------------------------------------------------------------------------
# 将项目根目录加入 sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# gsbag 相关
from gsbag import gsbag_reader

# proto 路径动态注入
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(base_dir)
ubm_mining_dir = os.path.join(project_root, 'data_mining', 'UBM_mining')
__proto_dir__ = os.path.join(ubm_mining_dir, 'ubm_data_mining/gsbag_parser/proto/v4.8.3')
sys.path.append(__proto_dir__)
sys.path.append(os.path.join(__proto_dir__, 'j6'))

from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2

# 使用本地复制的 image_handler，避免修改 data_mining 仓库
try:
    from tools.image_handler import ImageHandler, HevcDecoder
except ImportError:
    from image_handler import ImageHandler, HevcDecoder

try:
    from tools.camera_config import load_camera_config, build_camera_layout
except ImportError:
    from camera_config import load_camera_config, build_camera_layout


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
VIDEO_FPS = 10
MAX_CAMERA_SLOTS = 9    # 最大摄像头槽位数（3×3）


# ---------------------------------------------------------------------------
# 数据类定义
# ---------------------------------------------------------------------------
@dataclass
class FrameData:
    """单帧数据"""
    timestamp_ns: int
    image: Image.Image
    topic: str

@dataclass
class CameraChannel:
    """相机通道数据"""
    topic: str
    name: str
    frames: List[FrameData] = field(default_factory=list)
    current_idx: int = 0
    
    def __len__(self):
        return len(self.frames)


# ---------------------------------------------------------------------------
# 核心提取逻辑 - 多线程版本
# ---------------------------------------------------------------------------
class RosbagImageExtractor:
    """负责从 rosbag 中提取图片（多线程加速）"""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.image_handler = ImageHandler(
            workspace='',
            collect_date='',
            vehicle_name='',
            bag_type='',
            bag_id=''
        )

    @staticmethod
    def to_image_hevc_unit(cam_msg) -> Tuple[int, int, int, bytes]:
        """解析单个 Camera 消息"""
        msg = image_encode_boleidl_pb2.Image()
        image_data = []
        gsbag_reader.HobotMessageSerializer.deserialize_image(cam_msg, msg, image_data)
        return int(msg.time_stamp_us * 1000), msg.width, msg.height, image_data[0]

    def _find_bag_subdir(self, bag_path: str) -> str:
        """
        自动查找 rosbag 子目录。
        兼容两种格式：
        1. 旧格式：目录或子目录中包含 bag.bag
        2. 新格式：目录下直接包含多个 .bag 文件（如 camera.bag, default.bag）
        """
        if os.path.isfile(os.path.join(bag_path, "bag.bag")):
            return bag_path
        try:
            for item in os.listdir(bag_path):
                item_path = os.path.join(bag_path, item)
                if os.path.isdir(item_path) and os.path.isfile(os.path.join(item_path, "bag.bag")):
                    print(f"[INFO] 找到 rosbag 子目录: {item}")
                    return item_path
        except Exception as e:
            print(f"[WARNING] 扫描子目录失败: {e}")
        try:
            bag_files = [f for f in os.listdir(bag_path) if f.endswith('.bag')]
            if bag_files:
                print(f"[INFO] 找到新格式 rosbag 目录，包含: {bag_files}")
                return bag_path
        except Exception as e:
            print(f"[WARNING] 扫描 .bag 文件失败: {e}")
        return bag_path

    def extract_all_topics(self, bag_path: str, target_topics: List[str]) -> Dict[str, List[FrameData]]:
        """
        提取所有目标 topic 的图片到内存
        返回: {topic: [FrameData, ...]}
        """
        if not os.path.isdir(bag_path):
            raise ValueError(f"rosbag 路径不存在或不是目录: {bag_path}")
        
        actual_bag_path = self._find_bag_subdir(bag_path)
        if actual_bag_path != bag_path:
            print(f"[INFO] 使用实际 rosbag 路径: {actual_bag_path}")
        
        if "/mnt/gacrnd-oss" in actual_bag_path:
            print("[WARNING] 检测到 OSS 挂载路径，如果发生 I/O 错误，建议先将数据复制到本地磁盘")
        
        bag_file = os.path.join(actual_bag_path, "bag.bag")
        if not os.path.exists(bag_file):
            bag_files = [f for f in os.listdir(actual_bag_path) if f.endswith('.bag')]
            if not bag_files:
                raise ValueError(f"找不到 .bag 文件: {actual_bag_path}")

        print(f"[INFO] 开始读取 rosbag: {actual_bag_path}")
        bag_reader = gsbag_reader.GsBagReader(actual_bag_path)
        
        topic_messages = defaultdict(list)
        msg_count = 0
        for m in bag_reader.read_messages():
            if m.topic_name in target_topics:
                topic_messages[m.topic_name].append(m)
                msg_count += 1
                if msg_count % 100 == 0:
                    print(f"[INFO] 已读取 {msg_count} 条相机消息...")
        
        print(f"[INFO] 共读取 {msg_count} 条相机消息，开始并行解码...")
        
        result = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_topic = {}
            for topic, messages in topic_messages.items():
                future = executor.submit(self._decode_topic_frames, topic, messages)
                future_to_topic[future] = topic
            
            for future in as_completed(future_to_topic):
                topic = future_to_topic[future]
                try:
                    frames = future.result()
                    if frames:
                        result[topic] = frames
                        print(f"[INFO] {topic}: 解码完成，共 {len(frames)} 帧")
                except Exception as e:
                    print(f"[ERROR] {topic} 解码失败: {e}")
        
        return result

    def _decode_topic_frames(self, topic: str, messages: List) -> List[FrameData]:
        """解码单个 topic 的所有帧（逐帧解码，避免内存溢出）"""
        frames = []
        for m in messages:
            try:
                ts_ns, width, height, hevc_data = self.to_image_hevc_unit(m)
                hevc_io = io.BytesIO(hevc_data)
                decoded = HevcDecoder.decode_frames_v2(hevc_io)
                if decoded:
                    img = decoded[0].to_image().convert("RGB")
                    frame = FrameData(timestamp_ns=ts_ns, image=img, topic=topic)
                    frames.append(frame)
            except Exception as e:
                print(f"[WARNING] 解析 {topic} 消息失败: {e}")
        return frames


# ---------------------------------------------------------------------------
# 视频导出功能
# ---------------------------------------------------------------------------
class VideoExporter:
    """视频导出器"""
    
    @staticmethod
    def export_single_video(frames: List[FrameData], output_path: str, fps: int = 10) -> str:
        """导出单个视频"""
        if not frames:
            return None
        
        height, width = frames[0].image.shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
        
        for frame in frames:
            img_bgr = cv2.cvtColor(frame.image, cv2.COLOR_RGB2BGR)
            writer.write(img_bgr)
        
        writer.release()
        return output_path
    
    @staticmethod
    def export_multi_view_video(channels: Dict[str, CameraChannel], output_path: str, 
                                layout: List[List[Tuple]], fps: int = 10) -> str:
        """导出多视角拼接视频"""
        if not layout:
            return None
        
        max_frames = 0
        for row in layout:
            for cam_id, _ in row:
                if cam_id in channels:
                    max_frames = max(max_frames, len(channels[cam_id].frames))
        
        if max_frames == 0:
            return None
        
        cell_h, cell_w = 480, 640
        rows, cols = len(layout), max(len(r) for r in layout)
        out_h, out_w = cell_h * rows, cell_w * cols
        
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (out_w, out_h))
        
        print(f"[INFO] 开始导出多视角视频: {max_frames} 帧 @ {fps}fps")
        
        for frame_idx in range(max_frames):
            row_images = []
            for row in layout:
                cell_images = []
                for cam_id, _ in row:
                    if cam_id in channels and frame_idx < len(channels[cam_id].frames):
                        img = channels[cam_id].frames[frame_idx].image
                        img_resized = cv2.resize(img, (cell_w, cell_h))
                        img_bgr = cv2.cvtColor(img_resized, cv2.COLOR_RGB2BGR)
                        label = channels[cam_id].name
                        cv2.putText(img_bgr, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                                   0.7, (0, 255, 0), 2)
                        cell_images.append(img_bgr)
                    else:
                        cell_images.append(np.zeros((cell_h, cell_w, 3), dtype=np.uint8))
                
                if cell_images:
                    row_img = np.hstack(cell_images)
                    row_images.append(row_img)
            
            if row_images:
                frame_img = np.vstack(row_images)
                writer.write(frame_img)
            
            if (frame_idx + 1) % 100 == 0:
                print(f"[INFO] 已处理 {frame_idx + 1}/{max_frames} 帧...")
        
        writer.release()
        print(f"[INFO] 视频导出完成: {output_path}")
        return output_path


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
@dataclass
class PlayerState:
    """播放器状态"""
    bag_path: str = ""
    camera_config: Dict[str, dict] = field(default_factory=dict)
    camera_layout: List[List[Tuple]] = field(default_factory=list)
    channels: Dict[str, CameraChannel] = field(default_factory=dict)
    is_playing: bool = False
    play_speed: float = 1.0
    current_frame_idx: int = 0
    max_frames: int = 0
    output_dir: str = ""
    
    def reset(self):
        self.bag_path = ""
        self.camera_config = {}
        self.camera_layout = []
        self.channels.clear()
        self.is_playing = False
        self.play_speed = 1.0
        self.current_frame_idx = 0
        self.max_frames = 0
        if self.output_dir and os.path.isdir(self.output_dir):
            shutil.rmtree(self.output_dir, ignore_errors=True)
        self.output_dir = ""

STATE = PlayerState()


# ---------------------------------------------------------------------------
# Gradio 界面辅助函数
# ---------------------------------------------------------------------------
def _empty_load_result():
    """生成空的加载结果"""
    result = [gr.update(value="等待加载...")]
    # 隐藏所有 image slots
    for _ in range(MAX_CAMERA_SLOTS):
        result.append(gr.update(visible=False, value=None))
    # 隐藏所有 export buttons
    for _ in range(MAX_CAMERA_SLOTS):
        result.append(gr.update(visible=False))
    result.append(gr.update(maximum=1, value=0))
    result.append(gr.update(value="0 / 0"))
    return result


def load_bag(bag_path: str, progress=gr.Progress()):
    """加载 rosbag"""
    bag_path = bag_path.strip()
    if not bag_path:
        result = [gr.update(value="❌ 请输入有效的 rosbag 路径")]
        for _ in range(MAX_CAMERA_SLOTS):
            result.append(gr.update(value=None))
        for _ in range(MAX_CAMERA_SLOTS):
            result.append(gr.update(visible=False))
        result.append(gr.update(maximum=1, value=0))
        result.append(gr.update(value="0 / 0"))
        return result
    
    if not os.path.isdir(bag_path):
        result = [gr.update(value=f"❌ 路径不存在: {bag_path}")]
        for _ in range(MAX_CAMERA_SLOTS):
            result.append(gr.update(value=None))
        for _ in range(MAX_CAMERA_SLOTS):
            result.append(gr.update(visible=False))
        result.append(gr.update(maximum=1, value=0))
        result.append(gr.update(value="0 / 0"))
        return result
    
    # 清理旧数据
    STATE.reset()
    STATE.output_dir = tempfile.mkdtemp(prefix="rosbag_v2_")
    STATE.bag_path = bag_path
    
    # 加载摄像头配置
    camera_config = load_camera_config(bag_path)
    if not camera_config:
        # 如果没有找到 camera/virtual/ 配置，尝试使用默认配置（兼容旧格式）
        camera_config = {
            'fw120': {'topic': '/gac/cam/fw120_encoded', 'display_name': '前视120°'},
            'fw60': {'topic': '/gac/cam/fw60_encoded', 'display_name': '前视60°'},
            'ft30': {'topic': '/gac/cam/ft30_encoded', 'display_name': '前视30°'},
        }
    
    STATE.camera_config = camera_config
    STATE.camera_layout = build_camera_layout(camera_config)
    
    progress(0.1, desc="正在提取图片...")
    
    try:
        extractor = RosbagImageExtractor(max_workers=4)
        target_topics = [info['topic'] for info in camera_config.values()]
        topic_frames = extractor.extract_all_topics(bag_path, target_topics)
        
        if not topic_frames:
            result = [gr.update(value="❌ 未找到任何相机图片数据")]
            for _ in range(MAX_CAMERA_SLOTS):
                result.append(gr.update(visible=False, value=None))
            for _ in range(MAX_CAMERA_SLOTS):
                result.append(gr.update(visible=False))
            result.append(gr.update(maximum=1, value=0))
            result.append(gr.update(value="0 / 0"))
            return result
        
        # 构建 CameraChannel
        for cam_id, info in camera_config.items():
            topic = info['topic']
            if topic in topic_frames:
                STATE.channels[cam_id] = CameraChannel(topic=topic, name=info['display_name'], frames=topic_frames[topic])
        
        STATE.max_frames = max(len(ch) for ch in STATE.channels.values()) if STATE.channels else 0
        
        info = f"✅ 加载成功 | {len(STATE.channels)} 个相机 | 共 {STATE.max_frames} 帧"
        
        # 生成输出
        outputs = [gr.update(value=info)]
        
        # 按布局填充 Image 更新
        slot_idx = 0
        cam_id_list = []
        for row in STATE.camera_layout:
            for cam_id, display_name in row:
                cam_id_list.append(cam_id)
                if cam_id in STATE.channels and STATE.channels[cam_id].frames:
                    img = STATE.channels[cam_id].frames[0].image
                    outputs.append(gr.update(value=img))
                else:
                    outputs.append(gr.update(value=None))
                slot_idx += 1
        
        # 隐藏剩余的 image slots
        while slot_idx < MAX_CAMERA_SLOTS:
            outputs.append(gr.update(value=None))
            cam_id_list.append(None)
            slot_idx += 1
        
        # 更新导出按钮的可见性
        for i in range(MAX_CAMERA_SLOTS):
            if i < len(cam_id_list) and cam_id_list[i] is not None:
                outputs.append(gr.update(visible=True))
            else:
                outputs.append(gr.update(visible=False))
        
        outputs.append(gr.update(maximum=max(1, STATE.max_frames), value=1))
        outputs.append(gr.update(value=f"1 / {STATE.max_frames}"))
        
        return outputs
        
    except Exception as e:
        error_msg = str(e)
        if "disk I/O error" in error_msg or "SQLite" in error_msg:
            error_msg = (
                f"❌ 读取 rosbag 失败 (I/O 错误)\n\n"
                f"建议将数据复制到本地磁盘: cp -r '{bag_path}' /tmp/"
            )
        result = [gr.update(value=error_msg)]
        for _ in range(MAX_CAMERA_SLOTS):
            result.append(gr.update(value=None))
        for _ in range(MAX_CAMERA_SLOTS):
            result.append(gr.update(visible=False))
        result.append(gr.update(maximum=1, value=0))
        result.append(gr.update(value="0 / 0"))
        return result


def render_current_frame() -> List[Optional[np.ndarray]]:
    """渲染当前帧的所有视角"""
    idx = STATE.current_frame_idx
    images = []
    
    for row in STATE.camera_layout:
        for cam_id, _ in row:
            if cam_id in STATE.channels and idx < len(STATE.channels[cam_id].frames):
                img = STATE.channels[cam_id].frames[idx].image
                images.append(np.array(Image.fromarray(img)))
            else:
                images.append(None)
    
    return images


def on_frame_change(frame_num: int):
    """帧滑块变化"""
    STATE.current_frame_idx = max(0, min(frame_num - 1, STATE.max_frames - 1))
    images = render_current_frame()
    info = f"帧 {STATE.current_frame_idx + 1} / {STATE.max_frames}"
    
    # 填充到 MAX_CAMERA_SLOTS
    while len(images) < MAX_CAMERA_SLOTS:
        images.append(None)
    
    return images[:MAX_CAMERA_SLOTS] + [info]


def export_single_view(cam_id: str):
    """导出单个视角视频"""
    if cam_id not in STATE.channels or not STATE.channels[cam_id].frames:
        return None, f"❌ 该相机无数据: {cam_id}"
    
    output_path = os.path.join(STATE.output_dir, f"{cam_id}.mp4")
    VideoExporter.export_single_video(STATE.channels[cam_id].frames, output_path, VIDEO_FPS)
    
    return output_path, f"✅ 已导出: {cam_id}.mp4"


def export_multi_view():
    """导出多视角拼接视频"""
    if not STATE.channels:
        return None, "❌ 请先加载 rosbag"
    
    output_path = os.path.join(STATE.output_dir, "multi_view.mp4")
    result = VideoExporter.export_multi_view_video(STATE.channels, output_path, VIDEO_FPS, STATE.camera_layout)
    
    if result:
        return result, "✅ 多视角视频导出成功"
    return None, "❌ 导出失败"


# ---------------------------------------------------------------------------
# Gradio CSS
# ---------------------------------------------------------------------------
CSS = """
    .image-container img { max-height: 300px; object-fit: contain; }
    .camera-grid { display: grid; gap: 10px; }
    .info-text { font-family: monospace; font-size: 14px; }
"""

# ---------------------------------------------------------------------------
# 构建 Gradio 界面
# ---------------------------------------------------------------------------
def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Rosbag 多视角视频预览 V2") as demo:
        gr.Markdown("# 🎥 Rosbag 多视角视频预览工具 V2")
        gr.Markdown("支持多路相机同步预览、视频导出，动态适配 bag 中的摄像头配置")
        
        # 输入区域
        with gr.Row():
            bag_path_input = gr.Textbox(
                label="Rosbag 路径",
                placeholder="/root/data/bags/20260124_085515",
                scale=4,
            )
            load_btn = gr.Button("📂 加载", variant="primary", scale=1)
        
        status_text = gr.Textbox(label="状态", interactive=False, value="等待加载...")
        
        # 播放控制
        with gr.Row():
            frame_slider = gr.Slider(
                minimum=0, maximum=1, step=1, value=0,
                label="帧号",
                scale=4
            )
            frame_info = gr.Textbox(label="当前帧", value="0 / 0", interactive=False, scale=1)
        
        # 动态摄像头预览（最多 3×3 = 9 个）
        gr.Markdown("### 📷 相机预览")
        image_components = []
        for row_idx in range(3):
            with gr.Row():
                for col_idx in range(3):
                    img = gr.Image(
                        label=f"相机 {row_idx * 3 + col_idx + 1}",
                        interactive=False,
                        height=300,
                        visible=True,
                    )
                    image_components.append(img)
        
        # 视频导出
        gr.Markdown("### 📹 视频导出")
        with gr.Row():
            with gr.Column():
                gr.Markdown("**单视角导出**")
                export_buttons = []
                for i in range(MAX_CAMERA_SLOTS):
                    btn = gr.Button(f"导出: 相机 {i + 1}", visible=False)
                    export_buttons.append(btn)
            with gr.Column():
                gr.Markdown("**多视角导出**")
                export_multi_btn = gr.Button("📼 导出多视角拼接视频", variant="primary")
        
        export_result = gr.File(label="导出文件")
        export_status = gr.Textbox(label="导出状态", interactive=False)
        
        # 事件绑定
        load_outputs = [status_text] + image_components + export_buttons + [frame_slider, frame_info]
        load_btn.click(
            fn=load_bag,
            inputs=[bag_path_input],
            outputs=load_outputs,
        )
        
        frame_slider.change(
            fn=on_frame_change,
            inputs=[frame_slider],
            outputs=image_components + [frame_info],
        )
        
        # 单视角导出按钮事件
        for i, btn in enumerate(export_buttons):
            # 使用闭包捕获当前索引
            def make_export_fn(idx):
                def export_fn():
                    cam_ids = list(STATE.camera_config.keys())
                    if idx < len(cam_ids):
                        return export_single_view(cam_ids[idx])
                    return None, "❌ 无相机"
                return export_fn
            
            btn.click(
                fn=make_export_fn(i),
                outputs=[export_result, export_status],
            )
        
        # 多视角导出
        export_multi_btn.click(fn=export_multi_view, outputs=[export_result, export_status])
    
    return demo


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    demo = build_ui()
    demo.launch(server_name="0.0.0.0", server_port=30001, share=False, css=CSS)
