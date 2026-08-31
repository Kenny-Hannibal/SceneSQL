---
category: project
tags: Mage-VL,SGLang,VLM,codec-native,deploy,SQL-loop
---

Mage-VL 模型部署与推理方式 (2026-08-12 更新为SGLang):
- 模型路径: /root/models/Mage-VL/ (DSW大写; 本地工作区镜像 /data/var/models/Mage-VL/)
- 架构: MageVLForConditionalGeneration (codec-native VLM)
- 视觉编码器: Mage-ViT (from scratch, 448×448, 24层, hidden_size=1024)
- LLM backbone: Qwen3-4B-Instruct-2507 (hidden_size=2560, 36层)
- 4B 参数, bf16
- 核心优势: codec-native sparsity → 视觉token减少>75% → 3.5×推理加速
- ★ 推理方式: SGLang 0.5.7+ppu2.0.0 (平头哥官方PPU wheel直装, 无需源码编译/无需conda隔离), 端口31000, OpenAI-compatible API, 已实测
- ★ 2026-08-12: transformers裸推(mage_vl_server.py, conda env mage-vl)已停止弃用
- 启动: source /usr/local/PPU_SDK/envsetup.sh cuda; CUDA_VISIBLE_DEVICES=0 python3 -m sglang.launch_server --model-path /root/models/Mage-VL --trust-remote-code --port 31000 --host 0.0.0.0 --dtype bfloat16 --mem-fraction-static 0.6 --context-length 4096
- SQL Loop 验证环节: 直接把 bag 视频片段送进 Mage-VL, 不要抽帧, 利用 codec-native sparsity 优势
