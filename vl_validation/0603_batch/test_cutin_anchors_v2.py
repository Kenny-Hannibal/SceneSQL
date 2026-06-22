"""test_cutin_anchors_v2.py — 基于v4极简风格的更多变体测试"""
import sys, os, json, time
import numpy as np
import torch, torch.nn.functional as F
from pathlib import Path
from transformers import AutoModel, Qwen3VLProcessor
from qwen_vl_utils.vision_process import process_vision_info

MODEL_PATH = "/data/var/models/Qwen3-VL-Embedding-8B"
BASE = Path(__file__).parent

with open(BASE / "manifest.json") as f:
    manifest = json.load(f)
cutin_clips = [c for c in manifest if c["tag_name"] == "Cutin"]
img_paths = []
for clip in cutin_clips:
    for fn in clip["frames"]:
        fp = os.path.join(clip["clip_dir"], fn)
        if os.path.exists(fp):
            img_paths.append(fp)

print(f"Cutin frames: {len(img_paths)}")
processor = Qwen3VLProcessor.from_pretrained(MODEL_PATH, padding_side='right', trust_remote_code=True)
max_mem = {0: '6GiB', 1: '11GiB', 6: '5GiB', 7: '5GiB', 'cpu': '24GiB'}
model = AutoModel.from_pretrained(MODEL_PATH, trust_remote_code=True, torch_dtype=torch.float16,
    device_map='auto', max_memory=max_mem, offload_folder='offload')
model.eval()
first_dev = torch.device("cuda:0")
print(f"Model loaded")

def encode_images(paths, instruction):
    embs = []
    for p in paths:
        conv = [[{"role":"system","content":[{"type":"text","text":instruction}]},
                 {"role":"user","content":[{"type":"image","image":"file://"+p,"min_pixels":4*32*32,"max_pixels":1800*32*32}]}]]
        text = processor.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
        images, _, vk = process_vision_info(conv, image_patch_size=16, return_video_metadata=True, return_video_kwargs=True)
        inputs = processor(text=text, images=images, videos=None, video_metadata=None,
            truncation=True, max_length=2048, padding=True, do_resize=False, return_tensors="pt", **vk)
        moved = {k: v.to(first_dev) for k, v in inputs.items() if isinstance(v, torch.Tensor)}
        with torch.no_grad():
            out = model(**moved)
        mask = moved.get("attention_mask")
        last_idx = mask.sum(dim=1) - 1
        emb = F.normalize(out.last_hidden_state[torch.arange(out.last_hidden_state.size(0)), last_idx].float(), p=2, dim=1)
        embs.append(emb.cpu().numpy())
    return np.vstack(embs)

def encode_texts(texts, instruction):
    convs = [[{"role":"system","content":instruction},{"role":"user","content":t}] for t in texts]
    formatted = processor.apply_chat_template(convs, add_generation_prompt=True, tokenize=False)
    inputs = processor(text=formatted, images=None, videos=None, truncation=True, max_length=512, padding=True, return_tensors="pt")
    moved = {k: v.to(first_dev) for k, v in inputs.items() if isinstance(v, torch.Tensor)}
    with torch.no_grad():
        out = model(**moved)
    mask = moved.get("attention_mask")
    last_idx = mask.sum(dim=1) - 1
    emb = F.normalize(out.last_hidden_state[torch.arange(out.last_hidden_state.size(0)), last_idx].float(), p=2, dim=1)
    return emb.cpu().numpy()

# 图片编码(两种instruction)
print("Encoding images with 2 instructions...")
img_embs_search = encode_images(img_paths, "检索加塞场景")
img_embs_judge = encode_images(img_paths, "判断该画面中是否有加塞行为")
print(f"  search: shape={img_embs_search.shape}")
print(f"  judge:  shape={img_embs_judge.shape}")

variants = {
    "v4_minimal": {
        "inst": "检索加塞场景",
        "pos": "加塞:旁车变道插入自车前方",
        "negs": {"no_cutin": "没有加塞:旁车未变道"},
    },
    "v6_binary": {
        "inst": "判断该画面中是否有加塞行为",
        "pos": "是,画面中有加塞行为,旁车切入自车前方",
        "negs": {"no": "否,画面中没有加塞行为"},
    },
    "v7_action": {
        "inst": "检索加塞场景",
        "pos": "旁车正在变道切入自车前方",
        "negs": {"no_move": "旁车没有变道行为,保持原车道"},
    },
    "v8_occupy": {
        "inst": "检索加塞场景",
        "pos": "旁车占据自车前方空间,从侧方切入",
        "negs": {"clear": "自车前方空间开阔"},
    },
    "v9_occupy_binary": {
        "inst": "判断画面中旁车是否切入自车前方",
        "pos": "旁车切入自车前方",
        "negs": {"no_cutin": "旁车没有切入"},
    },
    "v10_noun_contrast": {
        "inst": "检索加塞场景",
        "pos": "旁车切入自车前方",
        "negs": {"no_cutin": "旁车未切入自车前方"},
    },
    "v11_yes_no": {
        "inst": "判断画面中是否有加塞",
        "pos": "有加塞",
        "negs": {"no": "无加塞"},
    },
}

print(f"\nTesting {len(variants)} variants...\n")
results = {}
for name, v in variants.items():
    texts = [v["pos"]] + list(v["negs"].values())
    labels = ["positive"] + [f"neg_{k}" for k in v["negs"]]
    anchor_embs = encode_texts(texts, v["inst"])

    imgs = img_embs_judge if ("判断" in v["inst"] or "是否" in v["inst"]) else img_embs_search
    sims = imgs @ anchor_embs.T
    pos_sims = sims[:, 0]
    neg_sims = np.max(sims[:, 1:], axis=1)
    margins = pos_sims - neg_sims

    avg_margin = float(np.mean(margins))
    pass_n = int(np.sum(margins > 0.03))
    pm_n = int(np.sum((margins > 0) & (margins <= 0.03)))
    fail_n = int(np.sum(margins <= 0))

    r = {
        "variant": name,
        "instruction": v["inst"],
        "positive": v["pos"],
        "negatives": v["negs"],
        "avg_pos_sim": round(float(np.mean(pos_sims)), 4),
        "avg_neg_sim": round(float(np.mean(neg_sims)), 4),
        "avg_margin": round(avg_margin, 4),
        "min_margin": round(float(np.min(margins)), 4),
        "max_margin": round(float(np.max(margins)), 4),
        "pass": pass_n, "pass_margin": pm_n, "fail": fail_n,
        "per_frame_margins": [round(float(m), 4) for m in margins],
    }
    results[name] = r
    print(f"  {name:25s} | pos={r['avg_pos_sim']:.4f} neg={r['avg_neg_sim']:.4f} margin={avg_margin:+.4f} | pass={pass_n} pm={pm_n} fail={fail_n}")

# 保存
with open(BASE / "cutin_anchor_comparison_v2.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

best = max(results.items(), key=lambda x: x[1]["avg_margin"])
print(f"\nBest: {best[0]} (margin={best[1]['avg_margin']:+.4f})")
print(f"  pos: {best[1]['positive']}")
print(f"  negs: {best[1]['negatives']}")
