"""run_vl_validation.py — VL Embedding 双模式验证 (CPU offload版)

两种编码模式:
  A) 逐帧编码 (per-frame): 每帧JPEG单独编码, 54条embedding
  B) Clip级编码 (clip-level): 同clip多帧作为video列表编码, 11条embedding

+ 交叉标签验证: 每帧 vs 所有4个标签锚点

输出:
  results_per_frame/   — 逐帧结果
  results_clip_level/  — clip级结果
  results_cross_tag/   — 交叉验证结果

用法:
  cd /data/var/workspace/projects/projects/SceneSQL/vl_validation/0603_batch
  python run_vl_validation.py
"""

import sys
import os
import json
import time
import csv
import numpy as np
import torch
import torch.nn.functional as F
from pathlib import Path
from PIL import Image
from transformers import AutoTokenizer, AutoModel, Qwen3VLProcessor
from qwen_vl_utils.vision_process import process_vision_info

# 路径
MODEL_PATH = "/data/var/models/Qwen3-VL-Embedding-8B"
MANIFEST_PATH = Path(__file__).parent / "manifest.json"
OUT_FRAME = Path(__file__).parent / "results_per_frame"
OUT_CLIP = Path(__file__).parent / "results_clip_level"
OUT_CROSS = Path(__file__).parent / "results_cross_tag"

# 目标标签
TARGET_TAGS = ["Cutin", "INTERSECTION_LEFTTURN", "INTERSECTION_RIGHTTURN", "INTERSECTION_STRAIGHT"]

# 文本锚点
TEXT_ANCHORS = {
    "Cutin": {
        "instruction": "判断画面中旁车是否切入自车前方",
        "positive": "旁车切入自车前方",
        "decomposition": {},
        "negatives": {
            "no_cutin": "旁车没有切入",
        },
    },
    "INTERSECTION_LEFTTURN": {
        "instruction": "检索自动驾驶场景中的路口左转",
        "positive": "自车在路口左转弯,穿越对向车道",
        "decomposition": {
            "left_turn": "自车在路口执行左转操作",
            "intersection": "场景发生在路口区域",
        },
        "negatives": {
            "straight": "自车直行通过路口",
            "right_turn": "自车在路口右转",
        },
    },
    "INTERSECTION_RIGHTTURN": {
        "instruction": "检索自动驾驶场景中的路口右转",
        "positive": "自车在路口右转弯",
        "decomposition": {
            "right_turn": "自车在路口执行右转操作",
            "intersection": "场景发生在路口区域",
        },
        "negatives": {
            "straight": "自车直行通过路口",
            "left_turn": "自车在路口左转",
        },
    },
    "INTERSECTION_STRAIGHT": {
        "instruction": "检索自动驾驶场景中的路口直行",
        "positive": "自车在路口直行通过,不转弯",
        "decomposition": {
            "straight": "自车保持直行方向",
            "intersection": "场景发生在路口区域",
        },
        "negatives": {
            "left_turn": "自车在路口左转",
            "right_turn": "自车在路口右转",
        },
    },
}


class VLEmbedder:
    """Qwen3-VL-Embedding编码器 (支持text/image/video, CPU offload)"""

    def __init__(self, model_path=MODEL_PATH):
        t0 = time.time()
        print(f"[VL-Embed] 加载模型: {model_path}")

        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self.processor = Qwen3VLProcessor.from_pretrained(model_path, padding_side='right', trust_remote_code=True)

        # GPU显存有限(vLLM占用大部分), 用offload策略
        max_mem = {
            0: "6GiB",
            1: "11GiB",
            6: "5GiB",
            7: "5GiB",
            "cpu": "24GiB",
        }
        self.model = AutoModel.from_pretrained(
            model_path,
            trust_remote_code=True,
            torch_dtype=torch.float16,
            device_map="auto",
            max_memory=max_mem,
            offload_folder="offload",
        )
        self.model.eval()
        self.dim = 4096

        # 找第一个GPU设备
        gpus = sorted(set(str(v) for v in self.model.hf_device_map.values() if str(v).startswith("cuda")))
        self._first_device = torch.device(gpus[0]) if gpus else torch.device("cpu")
        print(f"[VL-Embed] OK: {time.time()-t0:.1f}s, first_dev={self._first_device}, GPUs={gpus}")

    def _format_chat(self, text=None, image=None, video=None, instruction="Represent the input for semantic similarity retrieval."):
        """构造chat template格式的对话"""
        content = []
        conversation = [
            {"role": "system", "content": [{"type": "text", "text": instruction}]},
            {"role": "user", "content": content}
        ]

        if image:
            img_content = image if image.startswith(("http", "oss")) else "file://" + image
            content.append({"type": "image", "image": img_content,
                          "min_pixels": 4 * 32 * 32, "max_pixels": 1800 * 32 * 32})

        if video:
            if isinstance(video, list):
                # 帧列表作为video
                video_content = [("file://" + f if isinstance(f, str) else f) for f in video]
                content.append({"type": "video", "video": video_content,
                              "total_pixels": 10 * 768 * 32 * 32})
            else:
                vid_content = video if video.startswith(("http://", "https://")) else "file://" + video
                content.append({"type": "video", "video": vid_content,
                              "fps": 1, "max_frames": 64})

        if text:
            content.append({"type": "text", "text": text})

        if not content:
            content.append({"type": "text", "text": "NULL"})

        return conversation

    @torch.no_grad()
    def encode_text(self, texts, instruction="Represent the input for semantic similarity retrieval.", batch_size=4, max_length=512):
        """编码文本, 返回L2-normalized (N, 4096)"""
        all_embs = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            convs = [self._format_chat(text=t, instruction=instruction) for t in batch]
            embs = self._encode_conversations(convs, max_length=max_length)
            all_embs.append(embs)
        return np.vstack(all_embs) if all_embs else np.array([]).reshape(0, self.dim)

    @torch.no_grad()
    def encode_image(self, image_paths, instruction="Represent the input for semantic similarity retrieval.", batch_size=1, max_length=2048):
        """编码图片, 每张图一条embedding, 返回L2-normalized (N, 4096)"""
        all_embs = []
        for i in range(0, len(image_paths), batch_size):
            batch = image_paths[i:i+batch_size]
            convs = [self._format_chat(image=p, instruction=instruction) for p in batch]
            embs = self._encode_conversations(convs, max_length=max_length)
            all_embs.append(embs)
        return np.vstack(all_embs) if all_embs else np.array([]).reshape(0, self.dim)

    @torch.no_grad()
    def encode_video_frames(self, frame_groups, instruction="Represent the input for semantic similarity retrieval.", max_length=4096):
        """编码帧组(每clip一组), 每组一条embedding, 返回L2-normalized (N, 4096)
        frame_groups: list of list[str] — [[path1, path2, ...], [path3, path4, ...], ...]
        """
        all_embs = []
        for frames in frame_groups:
            conv = [self._format_chat(video=frames, instruction=instruction)]
            embs = self._encode_conversations(conv, max_length=max_length)
            all_embs.append(embs)
        return np.vstack(all_embs) if all_embs else np.array([]).reshape(0, self.dim)

    def _encode_conversations(self, conversations, max_length=2048):
        """内部: 对话列表 → embedding"""
        # apply_chat_template
        text = self.processor.apply_chat_template(
            conversations, add_generation_prompt=True, tokenize=False
        )

        # 处理视觉信息
        try:
            images, video_inputs, video_kwargs = process_vision_info(
                conversations, image_patch_size=16,
                return_video_metadata=True, return_video_kwargs=True
            )
        except Exception as e:
            print(f"  [WARN] vision_info error: {e}")
            images, video_inputs, video_kwargs = None, None, {"do_sample_frames": False}
            text = self.processor.apply_chat_template(
                [{"role": "user", "content": [{"type": "text", "text": "NULL"}]}],
                add_generation_prompt=True, tokenize=False
            )

        if video_inputs is not None:
            videos, video_metadata = zip(*video_inputs)
            videos = list(videos)
            video_metadata = list(video_metadata)
        else:
            videos, video_metadata = None, None

        inputs = self.processor(
            text=text, images=images, videos=videos, video_metadata=video_metadata,
            truncation=True, max_length=max_length, padding=True,
            do_resize=False, return_tensors="pt",
            **video_kwargs
        )

        # Move to device (handling offload)
        moved_inputs = {}
        for k, v in inputs.items():
            if isinstance(v, torch.Tensor):
                moved_inputs[k] = v.to(self._first_device)
            else:
                moved_inputs[k] = v

        outputs = self.model(**moved_inputs)
        hidden = outputs.last_hidden_state

        # lasttoken pooling
        mask = moved_inputs.get("attention_mask")
        if mask is None:
            emb = hidden[:, -1, :]
        else:
            last_idx = mask.sum(dim=1) - 1
            emb = hidden[torch.arange(hidden.size(0)), last_idx]

        # L2 normalize
        emb = F.normalize(emb.float(), p=2, dim=1)
        return emb.cpu().numpy()


def cosine_sim(a, b):
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return a @ b.T


def load_manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def get_target_clips(manifest):
    return [c for c in manifest if c["tag_name"] in TARGET_TAGS]


def encode_text_anchors(embedder, anchors_dict):
    """编码所有文本锚点"""
    all_anchors = {}
    for tag, template in anchors_dict.items():
        instruction = template["instruction"]
        texts = [template["positive"]]
        labels = ["positive"]
        for k, v in template["decomposition"].items():
            texts.append(v)
            labels.append(f"dec_{k}")
        for k, v in template["negatives"].items():
            texts.append(v)
            labels.append(f"neg_{k}")

        embs = embedder.encode_text(texts, instruction=instruction)
        all_anchors[tag] = {
            "labels": labels,
            "texts": texts,
            "embs": embs,
            "instruction": instruction,
        }
        print(f"  [锚点] {tag}: {len(labels)} 条, dim={embs.shape}")
    return all_anchors


def verdict_classify(pos_sim, max_neg):
    margin = pos_sim - max_neg
    if pos_sim > 0.90 and margin > 0.05:
        return "PASS"
    elif pos_sim > 0.85 and pos_sim > max_neg:
        return "PASS_MARGIN"
    elif pos_sim > 0.80:
        return "UNCERTAIN"
    else:
        return "FAIL"


VERDICT_ICON = {"PASS": "✅", "PASS_MARGIN": "⚠️", "UNCERTAIN": "❓", "FAIL": "❌"}


def save_results(results, out_dir, is_clip=False):
    """保存结果 (JSON + CSV + summary)"""
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "results.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # CSV
    if is_clip:
        cols = ["clip_idx", "tag", "frame_count", "verdict", "pos_sim", "max_neg_sim", "margin"]
    else:
        cols = ["clip_idx", "tag", "frame", "verdict", "pos_sim", "max_neg_sim", "margin"]

    with open(out_dir / "results.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in results:
            writer.writerow([r.get(c, "") for c in cols])

    # Summary
    summary = {}
    for r in results:
        tag = r["tag"]
        if tag not in summary:
            summary[tag] = {"total": 0, "PASS": 0, "PASS_MARGIN": 0, "UNCERTAIN": 0, "FAIL": 0,
                          "avg_pos_sim": [], "avg_margin": []}
        summary[tag]["total"] += 1
        summary[tag][r["verdict"]] += 1
        summary[tag]["avg_pos_sim"].append(r["pos_sim"])
        summary[tag]["avg_margin"].append(r["margin"])

    for tag, s in summary.items():
        s["avg_pos_sim"] = round(float(np.mean(s["avg_pos_sim"])), 4)
        s["avg_margin"] = round(float(np.mean(s["avg_margin"])), 4)

    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def print_summary(summary, mode_name):
    print(f"\n[{mode_name}] 汇总:")
    for tag, s in summary.items():
        pass_rate = (s["PASS"] + s["PASS_MARGIN"]) / s["total"] * 100 if s["total"] > 0 else 0
        print(f"  {tag:30s} | {s['total']:2d} | PASS={s['PASS']} PASS_M={s['PASS_MARGIN']} UNCERTAIN={s['UNCERTAIN']} FAIL={s['FAIL']} | avg_pos={s['avg_pos_sim']:.4f} avg_margin={s['avg_margin']:.4f} | pass_rate={pass_rate:.0f}%")


# ============================================================
# 模式A: 逐帧编码
# ============================================================
def run_per_frame(embedder, clips, anchors):
    print(f"\n{'='*60}")
    print(f"[模式A] 逐帧编码: {len(clips)} clips")
    print(f"{'='*60}")

    results = []
    t0 = time.time()

    for clip in clips:
        tag = clip["tag_name"]
        clip_dir = clip["clip_dir"]
        instruction = anchors[tag]["instruction"]

        for frame_name in clip["frames"]:
            frame_path = os.path.join(clip_dir, frame_name)
            if not os.path.exists(frame_path):
                print(f"  [WARN] 帧不存在: {frame_path}")
                continue

            # 编码单帧
            emb = embedder.encode_image([frame_path], instruction=instruction)
            if emb.shape[0] == 0:
                continue

            # 与锚点算相似度
            anchor_embs = anchors[tag]["embs"]
            anchor_labels = anchors[tag]["labels"]
            sims = cosine_sim(emb, anchor_embs)[0]

            sim_dict = {label: float(s) for label, s in zip(anchor_labels, sims)}
            pos_sim = sim_dict.get("positive", 0)
            max_neg = max(v for k, v in sim_dict.items() if k.startswith("neg_"))
            verdict = verdict_classify(pos_sim, max_neg)

            result = {
                "clip_idx": clip["clip_idx"],
                "tag": tag,
                "bag_id": clip["bag_id"],
                "frame": frame_name,
                "frame_path": frame_path,
                "verdict": verdict,
                "pos_sim": round(pos_sim, 4),
                "max_neg_sim": round(max_neg, 4),
                "margin": round(pos_sim - max_neg, 4),
                "similarity": {k: round(v, 4) for k, v in sim_dict.items()},
            }
            results.append(result)
            print(f"  {tag[:20]:20s} | {frame_name} | pos={pos_sim:.4f} neg={max_neg:.4f} m={pos_sim-max_neg:.4f} | {VERDICT_ICON.get(verdict,'?')} {verdict}")

    elapsed = time.time() - t0
    print(f"\n[模式A] 完成: {len(results)} 帧, {elapsed:.1f}s")

    summary = save_results(results, OUT_FRAME, is_clip=False)
    print_summary(summary, "模式A-逐帧")
    return results, summary


# ============================================================
# 模式B: Clip级编码
# ============================================================
def run_clip_level(embedder, clips, anchors):
    print(f"\n{'='*60}")
    print(f"[模式B] Clip级编码: {len(clips)} clips")
    print(f"{'='*60}")

    results = []
    t0 = time.time()

    for clip in clips:
        tag = clip["tag_name"]
        clip_dir = clip["clip_dir"]
        instruction = anchors[tag]["instruction"]

        frame_paths = [os.path.join(clip_dir, fn) for fn in clip["frames"]]
        frame_paths = [p for p in frame_paths if os.path.exists(p)]

        if not frame_paths:
            continue

        # 编码clip(多帧作为video)
        emb = embedder.encode_video_frames([frame_paths], instruction=instruction)
        if emb.shape[0] == 0:
            continue

        # 与锚点算相似度
        anchor_embs = anchors[tag]["embs"]
        anchor_labels = anchors[tag]["labels"]
        sims = cosine_sim(emb, anchor_embs)[0]

        sim_dict = {label: float(s) for label, s in zip(anchor_labels, sims)}
        pos_sim = sim_dict.get("positive", 0)
        max_neg = max(v for k, v in sim_dict.items() if k.startswith("neg_"))
        verdict = verdict_classify(pos_sim, max_neg)

        result = {
            "clip_idx": clip["clip_idx"],
            "tag": tag,
            "bag_id": clip["bag_id"],
            "frame_count": clip["frame_count"],
            "frames_used": len(frame_paths),
            "verdict": verdict,
            "pos_sim": round(pos_sim, 4),
            "max_neg_sim": round(max_neg, 4),
            "margin": round(pos_sim - max_neg, 4),
            "similarity": {k: round(v, 4) for k, v in sim_dict.items()},
        }
        results.append(result)
        print(f"  {tag[:20]:20s} | clip_{clip['clip_idx']:02d} | {len(frame_paths)}帧→1emb | pos={pos_sim:.4f} neg={max_neg:.4f} m={pos_sim-max_neg:.4f} | {VERDICT_ICON.get(verdict,'?')} {verdict}")

    elapsed = time.time() - t0
    print(f"\n[模式B] 完成: {len(results)} clips, {elapsed:.1f}s")

    summary = save_results(results, OUT_CLIP, is_clip=True)
    print_summary(summary, "模式B-Clip级")
    return results, summary


# ============================================================
# 交叉标签验证
# ============================================================
def run_cross_tag(embedder, clips, anchors):
    print(f"\n{'='*60}")
    print(f"[交叉验证] 每帧 vs 所有4个标签")
    print(f"{'='*60}")

    all_tags = list(anchors.keys())
    results = []

    for clip in clips:
        true_tag = clip["tag_name"]
        clip_dir = clip["clip_dir"]

        for frame_name in clip["frames"]:
            frame_path = os.path.join(clip_dir, frame_name)
            if not os.path.exists(frame_path):
                continue

            emb = embedder.encode_image([frame_path],
                instruction="Represent the input for semantic similarity retrieval.")
            if emb.shape[0] == 0:
                continue

            tag_sims = {}
            for tag in all_tags:
                anchor_embs = anchors[tag]["embs"]
                sims = cosine_sim(emb, anchor_embs)[0]
                pos_idx = anchors[tag]["labels"].index("positive")
                tag_sims[tag] = float(sims[pos_idx])

            best_tag = max(tag_sims, key=tag_sims.get)
            correct = best_tag == true_tag

            results.append({
                "clip_idx": clip["clip_idx"],
                "true_tag": true_tag,
                "frame": frame_name,
                "best_tag": best_tag,
                "best_sim": round(tag_sims[best_tag], 4),
                "true_tag_sim": round(tag_sims[true_tag], 4),
                "correct": correct,
                "all_sims": {k: round(v, 4) for k, v in tag_sims.items()},
            })

    OUT_CROSS.mkdir(parents=True, exist_ok=True)
    with open(OUT_CROSS / "results.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # CSV
    with open(OUT_CROSS / "results.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["clip_idx", "true_tag", "frame", "best_tag", "correct", "true_tag_sim", "best_sim"])
        for r in results:
            writer.writerow([r["clip_idx"], r["true_tag"], r["frame"], r["best_tag"], r["correct"], r["true_tag_sim"], r["best_sim"]])

    correct_count = sum(1 for r in results if r["correct"])
    total = len(results)
    print(f"\n[交叉验证] {correct_count}/{total} 帧的best-match标签与真实标签一致 ({correct_count/total*100:.1f}%)")

    for tag in all_tags:
        tag_results = [r for r in results if r["true_tag"] == tag]
        if not tag_results:
            continue
        correct = sum(1 for r in tag_results if r["correct"])
        print(f"  {tag:30s} | {correct}/{len(tag_results)} correct")

    return results


def main():
    print("=" * 60)
    print("VL Embedding 双模式验证")
    print(f"目标标签: {TARGET_TAGS}")
    print("=" * 60)

    manifest = load_manifest()
    clips = get_target_clips(manifest)
    print(f"目标clips: {len(clips)}, 总帧数: {sum(c['frame_count'] for c in clips)}")

    # 加载模型
    embedder = VLEmbedder()

    # 编码文本锚点
    print("\n[步骤1] 编码文本锚点...")
    anchors = encode_text_anchors(embedder, TEXT_ANCHORS)

    # 模式A: 逐帧
    print("\n[步骤2] 逐帧编码...")
    frame_results, frame_summary = run_per_frame(embedder, clips, anchors)

    # 模式B: Clip级
    print("\n[步骤3] Clip级编码...")
    clip_results, clip_summary = run_clip_level(embedder, clips, anchors)

    # 交叉验证
    print("\n[步骤4] 交叉标签验证...")
    cross_results = run_cross_tag(embedder, clips, anchors)

    # 最终对比
    print(f"\n{'='*60}")
    print(f"[最终对比]")
    print(f"{'='*60}")
    for tag in TARGET_TAGS:
        fs = frame_summary.get(tag, {})
        cs = clip_summary.get(tag, {})
        f_pass = (fs.get("PASS", 0) + fs.get("PASS_MARGIN", 0)) / max(fs.get("total", 1), 1) * 100
        c_pass = (cs.get("PASS", 0) + cs.get("PASS_MARGIN", 0)) / max(cs.get("total", 1), 1) * 100
        print(f"  {tag:30s} | 逐帧pass={f_pass:.0f}% (pos={fs.get('avg_pos_sim',0):.4f}) | clip级pass={c_pass:.0f}% (pos={cs.get('avg_pos_sim',0):.4f})")

    print(f"\n结果文件:")
    print(f"  逐帧: {OUT_FRAME}/")
    print(f"  clip级: {OUT_CLIP}/")
    print(f"  交叉验证: {OUT_CROSS}/")


if __name__ == "__main__":
    main()
