"""test_cutin_anchors.py — 测试多种Cutin锚点变体

对同一批Cutin帧图片, 用不同的文本锚点编码, 比较margin
不需要改路口标签的锚点(已验证通过), 只优化Cutin
"""

import sys
import os
import json
import time
import numpy as np
import torch
import torch.nn.functional as F
from pathlib import Path
from transformers import AutoTokenizer, AutoModel, Qwen3VLProcessor
from qwen_vl_utils.vision_process import process_vision_info

MODEL_PATH = "/data/var/models/Qwen3-VL-Embedding-8B"
BASE = Path("/data/var/workspace/projects/projects/SceneSQL/vl_validation/0603_batch")

# ============================================================
# 多种Cutin锚点变体
# ============================================================
CUTIN_VARIANTS = {
    "v0_original": {
        "instruction": "检索自动驾驶场景中的加塞/插队",
        "positive": "旁车从相邻车道强行切入自车前方,距离突然变近",
        "decomposition": {
            "lateral_move": "旁车横向移动进入自车车道",
            "proximity": "旁车与自车纵向距离变小",
        },
        "negatives": {
            "normal_merge": "旁车正常并道,提前打转向灯,保持安全距离",
            "parallel_cruise": "旁车与自车并行行驶,没有变道",
        },
    },
    "v1_visual_concrete": {
        "instruction": "检索自动驾驶场景中的加塞/插队",
        "positive": "相邻车道车辆以明显横摆角切入自车正前方,占据自车行驶空间,两车距离迅速缩短",
        "decomposition": {
            "lateral_move": "旁车跨越车道线横向移动进入自车所在车道",
            "close_proximity": "旁车切入后位于自车前方近距离位置",
        },
        "negatives": {
            "far_ahead": "前方远处有车辆同向行驶,距离远,无变道行为",
            "stable_lane": "旁车在相邻车道保持直线行驶,没有跨越车道线",
        },
    },
    "v2_first_person": {
        "instruction": "检索自动驾驶场景中的加塞/插队",
        "positive": "画面中一辆车从右侧车道斜向切入,出现在自车正前方很近的位置",
        "decomposition": {
            "cut_in": "车辆从侧方车道切入到自车前方",
            "close": "切入车辆距离自车很近",
        },
        "negatives": {
            "empty_road": "画面中自车前方空旷,远处没有车辆",
            "parallel": "画面中右侧车道有车辆平行行驶,没有变道",
        },
    },
    "v3_contrast_extreme": {
        "instruction": "检索自动驾驶场景中的加塞/插队",
        "positive": "旁车突然变道插入自车车道,车头出现在自车前方,横摆角大,间距急缩",
        "decomposition": {
            "sudden_lateral": "旁车突然横向移动跨越车道线",
            "intrude": "旁车车头侵入自车车道前方",
        },
        "negatives": {
            "far_vehicle": "前方远处有车,距离大于50米,无变道",
            "same_direction": "旁车在同一车道或相邻车道同向行驶,无横向移动",
        },
    },
    "v4_minimal": {
        "instruction": "检索自动驾驶场景中的加塞/插队",
        "positive": "加塞:旁车变道插入自车前方",
        "decomposition": {},
        "negatives": {
            "no_cutin": "没有加塞:旁车未变道",
        },
    },
    "v5_chinese_colloquial": {
        "instruction": "检索加塞插队场景",
        "positive": "旁边车道的车硬挤过来插到我前面,离得很近",
        "decomposition": {
            "squeeze": "旁车硬挤过来变道",
            "close": "插进来之后离得很近",
        },
        "negatives": {
            "far": "前面很远才有车,旁边车道的车也没过来",
            "stay": "旁边的车各走各的道,没人变道",
        },
    },
}


class VLEmbedder:
    def __init__(self, model_path=MODEL_PATH):
        t0 = time.time()
        self.processor = Qwen3VLProcessor.from_pretrained(model_path, padding_side='right', trust_remote_code=True)
        max_mem = {0: "6GiB", 1: "11GiB", 6: "5GiB", 7: "5GiB", "cpu": "24GiB"}
        self.model = AutoModel.from_pretrained(
            model_path, trust_remote_code=True, torch_dtype=torch.float16,
            device_map="auto", max_memory=max_mem, offload_folder="offload",
        )
        self.model.eval()
        gpus = sorted(set(str(v) for v in self.model.hf_device_map.values() if str(v).startswith("cuda")))
        self._first_device = torch.device(gpus[0]) if gpus else torch.device("cpu")
        print(f"[VL] loaded in {time.time()-t0:.1f}s, dev={self._first_device}")

    @torch.no_grad()
    def encode_text(self, texts, instruction, batch_size=8, max_length=512):
        all_embs = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            convs = [[
                {"role": "system", "content": [{"type": "text", "text": instruction}]},
                {"role": "user", "content": [{"type": "text", "text": t}]},
            ] for t in batch]
            formatted = self.processor.apply_chat_template(convs, add_generation_prompt=True, tokenize=False)
            inputs = self.processor(text=formatted, images=None, videos=None,
                truncation=True, max_length=max_length, padding=True, return_tensors="pt")
            moved = {k: v.to(self._first_device) for k, v in inputs.items() if isinstance(v, torch.Tensor)}
            out = self.model(**moved)
            mask = moved.get("attention_mask")
            last_idx = mask.sum(dim=1) - 1
            emb = out.last_hidden_state[torch.arange(out.last_hidden_state.size(0)), last_idx]
            emb = F.normalize(emb.float(), p=2, dim=1)
            all_embs.append(emb.cpu().numpy())
        return np.vstack(all_embs)

    @torch.no_grad()
    def encode_images(self, image_paths, instruction, max_length=2048):
        all_embs = []
        for img_path in image_paths:
            conv = [[
                {"role": "system", "content": [{"type": "text", "text": instruction}]},
                {"role": "user", "content": [
                    {"type": "image", "image": "file://" + img_path,
                     "min_pixels": 4*32*32, "max_pixels": 1800*32*32},
                ]},
            ]]
            text = self.processor.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
            images, _, video_kwargs = process_vision_info(
                conv, image_patch_size=16, return_video_metadata=True, return_video_kwargs=True
            )
            inputs = self.processor(text=text, images=images, videos=None, video_metadata=None,
                truncation=True, max_length=max_length, padding=True, do_resize=False, return_tensors="pt",
                **video_kwargs)
            moved = {k: v.to(self._first_device) for k, v in inputs.items() if isinstance(v, torch.Tensor)}
            out = self.model(**moved)
            mask = moved.get("attention_mask")
            last_idx = mask.sum(dim=1) - 1
            emb = out.last_hidden_state[torch.arange(out.last_hidden_state.size(0)), last_idx]
            emb = F.normalize(emb.float(), p=2, dim=1)
            all_embs.append(emb.cpu().numpy())
        return np.vstack(all_embs)


def cosine_sim(a, b):
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return a @ b.T


def main():
    # 加载manifest, 只取Cutin clips
    with open(BASE / "manifest.json") as f:
        manifest = json.load(f)
    cutin_clips = [c for c in manifest if c["tag_name"] == "Cutin"]
    print(f"Cutin clips: {len(cutin_clips)}, frames: {sum(c['frame_count'] for c in cutin_clips)}")

    # 加载模型
    embedder = VLEmbedder()

    # 先编码所有Cutin帧图片(用通用instruction, 后面换锚点不用重新编码图)
    # 但不同instruction会影响图片编码... 这里用通用instruction
    # 先试一下: 用同一批图片, 不同锚点文本
    instruction = "检索自动驾驶场景中的加塞/插队"

    print("\n[1] 编码Cutin帧图片...")
    img_paths = []
    for clip in cutin_clips:
        for fn in clip["frames"]:
            fp = os.path.join(clip["clip_dir"], fn)
            if os.path.exists(fp):
                img_paths.append(fp)
    print(f"  {len(img_paths)} 帧")

    t0 = time.time()
    img_embs = embedder.encode_images(img_paths, instruction=instruction)
    print(f"  编码完成: {img_embs.shape}, {time.time()-t0:.1f}s")

    # 测试每种变体
    results = {}
    print(f"\n[2] 测试 {len(CUTIN_VARIANTS)} 种Cutin锚点变体...\n")

    for variant_name, template in CUTIN_VARIANTS.items():
        inst = template["instruction"]
        texts = [template["positive"]]
        labels = ["positive"]
        for k, v in template.get("decomposition", {}).items():
            texts.append(v)
            labels.append(f"dec_{k}")
        for k, v in template.get("negatives", {}).items():
            texts.append(v)
            labels.append(f"neg_{k}")

        # 编码锚点
        anchor_embs = embedder.encode_text(texts, instruction=inst)

        # 计算相似度
        sims = cosine_sim(img_embs, anchor_embs)  # (N_img, N_anchor)
        pos_idx = labels.index("positive")
        neg_indices = [i for i, l in enumerate(labels) if l.startswith("neg_")]

        pos_sims = sims[:, pos_idx]
        max_neg_sims = np.max(sims[:, neg_indices], axis=1) if neg_indices else np.zeros(len(img_paths))
        margins = pos_sims - max_neg_sims

        # 统计
        avg_pos = float(np.mean(pos_sims))
        avg_neg = float(np.mean(max_neg_sims))
        avg_margin = float(np.mean(margins))
        pass_count = int(np.sum(margins > 0.03))
        pass_margin_count = int(np.sum((margins > 0) & (margins <= 0.03)))
        fail_count = int(np.sum(margins <= 0))

        result = {
            "variant": variant_name,
            "positive": template["positive"],
            "negatives": template.get("negatives", {}),
            "avg_pos_sim": round(avg_pos, 4),
            "avg_max_neg_sim": round(avg_neg, 4),
            "avg_margin": round(avg_margin, 4),
            "min_margin": round(float(np.min(margins)), 4),
            "max_margin": round(float(np.max(margins)), 4),
            "pass_rate": f"{pass_count+pass_margin_count}/{len(img_paths)}",
            "detail": {
                "pass": pass_count,
                "pass_margin": pass_margin_count,
                "uncertain_fail": fail_count,
            }
        }
        results[variant_name] = result

        print(f"  {variant_name:25s} | pos={avg_pos:.4f} neg={avg_neg:.4f} margin={avg_margin:+.4f} | pass={pass_count} pm={pass_margin_count} fail={fail_count} | {template['positive'][:30]}")

    # 保存结果
    out_path = BASE / "cutin_anchor_comparison.json"
    with open(out_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 找最佳
    best = max(results.items(), key=lambda x: x[1]["avg_margin"])
    print(f"\n最佳变体: {best[0]} (avg_margin={best[1]['avg_margin']:+.4f})")
    print(f"  positive: {best[1]['positive']}")
    print(f"  negatives: {best[1]['negatives']}")

    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
