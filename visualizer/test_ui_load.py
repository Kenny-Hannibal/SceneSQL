#!/usr/bin/env python3
"""测试 load_bag 返回值是否符合 Gradio 预期"""
import sys
import os
sys.path.insert(0, '/root/data/text2sql')
os.chdir('/root/data/text2sql')

# 设置 LD_LIBRARY_PATH
os.environ['LD_LIBRARY_PATH'] = (
    '/root/data/text2sql/.venv/lib:'
    '/root/.local/share/uv/python/cpython-3.10.19-linux-x86_64-gnu/lib:'
    '/root/data/text2sql/three_party/gsbag_x86_Release_4.2.18_20260227_Linux/external/platform_sdk/lib/gacrnd:'
    '/root/data/text2sql/three_party/gsbag_x86_Release_4.2.18_20260227_Linux/external/platform_sdk/lib/third_party'
)

import gradio as gr
from tools.rosbag_image_visualizer import load_bag, STATE, MAX_CAMERA_SLOTS, build_ui

print("Testing load_bag...")
result = load_bag('/root/data/bags/20260124_085515')
print(f"Return count: {len(result)}")
print(f"Expected count: {1 + MAX_CAMERA_SLOTS + MAX_CAMERA_SLOTS + 2}")

for i, r in enumerate(result):
    if hasattr(r, 'value'):
        val = r.value
        if isinstance(val, str):
            print(f"[{i}] value(str): {val[:80]}")
        elif val is None:
            print(f"[{i}] value: None")
        else:
            print(f"[{i}] value type: {type(val).__name__}")
    else:
        print(f"[{i}] type: {type(r).__name__}")

# 检查 STATE
print(f"\nSTATE.channels: {list(STATE.channels.keys())}")
print(f"STATE.max_frames: {STATE.max_frames}")
print(f"STATE.camera_layout: {STATE.camera_layout}")

# 测试 on_frame_change
from tools.rosbag_image_visualizer import on_frame_change
result2 = on_frame_change(1)
print(f"\non_frame_change(1) count: {len(result2)}")
for i, r in enumerate(result2):
    if r is not None:
        print(f"[{i}] shape: {r.shape}, dtype: {r.dtype}")
    else:
        print(f"[{i}] None")
