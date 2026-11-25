"""T1 spike: CLIP ViT-B/32 (zero-shot over vocab.py) vs RAM++ (4,585 tags) on COCO val2017.

Run by .github/workflows/spike-t1.yml, which downloads the models and COCO first:
    python t1_tagging.py --models DIR --coco DIR --n 500 --out report.json

Quality: COCO's human labels are the truth. Only "salient" objects count (instances
covering >= 2 % of the image); tags outside COCO's 80 categories are not scored. Speed is
the image model alone (tag text embeddings are computed once).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer

from vocab import ALL_TAGS, COCO_TO_TAG

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
SALIENT = 0.02


def session(path: Path, threads: int) -> ort.InferenceSession:
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


# ------------------------------------------------------------------ CLIP
def clip_pixels(img: Image.Image) -> np.ndarray:
    w, h = img.size
    s = 224 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(img.crop((left, top, left + 224, top + 224)), np.float32) / 255
    return ((a - MEAN) / STD).transpose(2, 0, 1)[None]


class Clip:
    def __init__(self, d: Path, variant: str, threads: int):
        sfx = "" if variant == "fp32" else "_quantized"
        self.vision = session(d / f"vision_model{sfx}.onnx", threads)
        self.text = session(d / f"text_model{sfx}.onnx", threads)
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_padding(length=77, pad_id=49407)
        self.tok.enable_truncation(77)
        self.name = f"CLIP ViT-B/32 ({variant})"

    def _run_text(self, texts: list[str]) -> np.ndarray:
        enc = self.tok.encode_batch(texts)
        feeds = {"input_ids": np.array([e.ids for e in enc], np.int64)}
        if "attention_mask" in {i.name for i in self.text.get_inputs()}:
            feeds["attention_mask"] = np.array([e.attention_mask for e in enc], np.int64)
        names = [o.name for o in self.text.get_outputs()]
        out = self.text.run(["text_embeds"] if "text_embeds" in names else [names[0]], feeds)[0]
        return out / np.linalg.norm(out, axis=1, keepdims=True)

    def tag_matrix(self, tags: list[str]) -> np.ndarray:
        prompts = [["a photo of {}.", "a photo of a {}.", "an image showing {}."]]
        embs = np.stack([self._run_text([p.format(t) for t in tags]) for p in prompts[0]])
        m = embs.mean(0)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    def embed(self, img: Image.Image) -> np.ndarray:
        names = [o.name for o in self.vision.get_outputs()]
        out = self.vision.run(["image_embeds"] if "image_embeds" in names else [names[0]], {"pixel_values": clip_pixels(img)})[0][0]
        return out / np.linalg.norm(out)


# ------------------------------------------------------------------ RAM++
class RamPlus:
    def __init__(self, d: Path, threads: int):
        self.s = session(d / "onnx" / "vision_model_int8.onnx", threads)
        self.label_embed = np.load(d / "tag_embeddings.npy")
        self.tags = (d / "ram_tag_list.txt").read_text(encoding="utf-8").splitlines()
        self.thresholds = np.loadtxt(d / "ram_tag_list_threshold.txt")
        self.name = "RAM++ Swin-L (int8)"

    def scores(self, img: Image.Image) -> np.ndarray:
        a = (np.asarray(img.resize((384, 384), Image.BILINEAR), np.float32) / 255).transpose(2, 0, 1)[None]
        return self.s.run(["tag_scores"], {"pixel_values": a, "label_embed": self.label_embed})[0][0]


RAM_SYNONYMS = {
    "person": ["person", "man"], "sports ball": ["ball", "soccer ball"], "cell phone": ["cellphone", "phone", "mobile phone"],
    "tv": ["television", "tv"], "dining table": ["dinning table", "dining table", "table"], "couch": ["couch", "sofa"],
    "potted plant": ["potted plant", "houseplant", "plant"], "hair drier": ["hair drier", "hair dryer", "blow dryer"],
    "skis": ["ski", "skis"], "motorcycle": ["motorbike", "motorcycle"], "airplane": ["airplane", "plane", "aircraft"],
    "donut": ["donut", "doughnut"], "remote": ["remote", "remote control"], "mouse": ["computer mouse", "mouse"],
    "wine glass": ["wine glass"], "baseball glove": ["baseball glove", "glove"], "hot dog": ["hot dog"],
}


# ------------------------------------------------------------------ COCO
def load_coco(coco: Path, n: int, seed: int = 1) -> list[tuple[Path, set[str], set[str], str]]:
    ann = json.loads((coco / "instances_val2017.json").read_text())
    cats = {c["id"]: c["name"] for c in ann["categories"]}
    imgs = {i["id"]: i for i in ann["images"]}
    allc: dict[int, set[str]] = defaultdict(set)
    sal: dict[int, set[str]] = defaultdict(set)
    for a in ann["annotations"]:
        im = imgs[a["image_id"]]
        name = cats[a["category_id"]]
        allc[a["image_id"]].add(name)
        if a["area"] >= SALIENT * im["width"] * im["height"]:
            sal[a["image_id"]].add(name)
    ids = sorted(i for i in sal if (coco / "val2017" / imgs[i]["file_name"]).exists())
    random.Random(seed).shuffle(ids)
    return [(coco / "val2017" / imgs[i]["file_name"], sal[i], allc[i], imgs[i]["coco_url"]) for i in ids[:n]]


def prf(pred: list[set[str]], salient: list[set[str]], anyc: list[set[str]]) -> dict:
    """Precision counts a tag right if the object is in the photo at all; recall needs salient objects."""
    tp = sum(len(p & a) for p, a in zip(pred, anyc))
    npred = sum(len(p) for p in pred)
    hit = sum(len(p & s) for p, s in zip(pred, salient))
    nsal = sum(len(s) for s in salient)
    p = tp / npred if npred else 0.0
    r = hit / nsal if nsal else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "tags_per_image": round(npred / len(pred), 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--coco", type=Path, required=True)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-ram", action="store_true")
    args = ap.parse_args()

    data = load_coco(args.coco, args.n)
    salient = [s for _, s, _, _ in data]
    anyc = [a for _, _, a, _ in data]
    coco_tags = {COCO_TO_TAG[c]: c for c in COCO_TO_TAG}
    report: dict = {"machine": {"os": platform.platform(), "cpu": platform.processor() or platform.machine(),
                                "cores": os.cpu_count(), "onnxruntime": ort.__version__},
                    "images": len(data), "salient_fraction": SALIENT, "models": {}, "samples": []}
    samples = {i: {"url": data[i][3], "coco_labels": sorted(data[i][2])} for i in range(min(16, len(data)))}

    # ---- CLIP variants
    for variant in ("fp32", "quantized"):
        clip = Clip(args.models / "clip", variant, args.threads)
        tmat = clip.tag_matrix(ALL_TAGS)
        sims, times = [], []
        for path, *_ in data:
            img = Image.open(path).convert("RGB")
            t = time.perf_counter()
            e = clip.embed(img)
            times.append((time.perf_counter() - t) * 1000)
            sims.append(tmat @ e)
        sims = np.array(sims)
        probs = np.exp(100 * sims) / np.exp(100 * sims).sum(1, keepdims=True)
        sweep = {}
        for mode, mat, grid in (("softmax", probs, [0.01, 0.02, 0.03, 0.05, 0.08, 0.1, 0.15, 0.2]),
                                ("cosine", sims, [0.22, 0.23, 0.24, 0.25, 0.26, 0.27, 0.28, 0.3])):
            for th in grid:
                pred = []
                for row in mat:
                    top = [i for i in np.argsort(-row)[:8] if row[i] >= th]
                    pred.append({coco_tags[ALL_TAGS[i]] for i in top if ALL_TAGS[i] in coco_tags})
                sweep[f"{mode}>={th}"] = prf(pred, salient, anyc)
        best = max(sweep, key=lambda k: sweep[k]["f1"])
        report["models"][clip.name] = {
            "ms_per_image": {"median": round(statistics.median(times), 1), "p95": round(np.percentile(times, 95), 1)},
            "best": {best: sweep[best]}, "sweep": sweep,
            "size_mb": round(sum((args.models / "clip" / f).stat().st_size for f in os.listdir(args.models / "clip")
                                 if f.endswith(".onnx") and (("quantized" in f) == (variant == "quantized"))) / 1e6, 1),
        }
        mode, th = best.split(">=")
        mat = probs if mode == "softmax" else sims
        for i in samples:
            row = mat[i]
            samples[i][clip.name] = [f"{ALL_TAGS[j]} {row[j]:.2f}" for j in np.argsort(-row)[:8] if row[j] >= float(th)]

    # ---- RAM++
    if not args.skip_ram:
        ram = RamPlus(args.models / "ram", args.threads)
        idx = {t: i for i, t in enumerate(ram.tags)}
        cmap, missing = {}, []
        for c in COCO_TO_TAG:
            hit = next((idx[s] for s in [c, *RAM_SYNONYMS.get(c, [])] if s in idx), None)
            (cmap.__setitem__(c, hit) if hit is not None else missing.append(c))
        allscores, times = [], []
        for path, *_ in data:
            img = Image.open(path).convert("RGB")
            t = time.perf_counter()
            allscores.append(ram.scores(img))
            times.append((time.perf_counter() - t) * 1000)
        allscores = np.array(allscores)
        sweep = {}
        for scale in (0.8, 1.0, 1.2):
            pred = [{c for c, j in cmap.items() if row[j] > ram.thresholds[j] * scale} for row in allscores]
            sweep[f"threshold x{scale}"] = prf(pred, [s & set(cmap) for s in salient], anyc)
        best = max(sweep, key=lambda k: sweep[k]["f1"])
        report["models"][ram.name] = {
            "ms_per_image": {"median": round(statistics.median(times), 1), "p95": round(np.percentile(times, 95), 1)},
            "best": {best: sweep[best]}, "sweep": sweep, "coco_categories_not_in_ram": missing,
            "size_mb": round((args.models / "ram" / "onnx" / "vision_model_int8.onnx").stat().st_size / 1e6, 1),
        }
        for i in samples:
            row = allscores[i]
            on = [j for j in np.argsort(-row) if row[j] > ram.thresholds[j]][:12]
            samples[i][ram.name] = [f"{ram.tags[j]} {row[j]:.2f}" for j in on]

    report["samples"] = list(samples.values())
    args.out.write_text(json.dumps(report, indent=2))
    for name, m in report["models"].items():
        print(name, m["ms_per_image"], m["best"], f"{m['size_mb']} MB")


if __name__ == "__main__":
    main()
