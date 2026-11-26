"""AI stages (T2): CLIP image embedding (search, similar photos) and RAM++ tags.

Both read the 512 px thumbnail the thumbnail stage already made, never the original: it is
small, already oriented, and exists for every format we can display (RAW and HEIC too).
Choices measured in the T1 spike on COCO val2017: RAM++ tags F1 0.81 (precision 0.76),
CLIP is kept for search because it maps text and images into the same space.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import StageError

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
MAX_TAGS = 8

# RAM++ tags that describe the picture rather than its content, or are too vague to filter by.
GENERIC_TAGS = frozenset(
    {
        "image",
        "photo",
        "picture",
        "stock image",
        "illustration",
        "background",
        "view",
        "color",
        "white",
        "black",
        "stand",
        "sit",
        "hold",
        "stare",
        "pose",
        "look",
        "wear",
        "walk",
        "other",
    }
)


def _session(path: Path, threads: int):
    import onnxruntime as ort  # heavy: loaded only where AI runs (workers, and the engine for search text)

    so = ort.SessionOptions()
    if threads > 0:
        so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.enable_cpu_mem_arena = False  # give memory back between photos
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def load_rgb(path: Path) -> Image.Image:
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except (OSError, ValueError) as e:
        raise StageError("thumbnail_unreadable", f"{path.name}: {e}") from e


def clip_pixels(img: Image.Image) -> np.ndarray:
    """CLIP preprocessing: shortest side to 224 (bicubic), centre crop, normalise."""
    w, h = img.size
    s = 224 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(img.crop((left, top, left + 224, top + 224)), np.float32) / 255
    return ((a - CLIP_MEAN) / CLIP_STD).transpose(2, 0, 1)[None]


class ClipImage:
    def __init__(self, models: Path, threads: int = 0):
        self.s = _session(models / "clip" / "vision_model_quantized.onnx", threads)
        names = [o.name for o in self.s.get_outputs()]
        self.out = "image_embeds" if "image_embeds" in names else names[0]

    def embed(self, img: Image.Image) -> np.ndarray:
        v = self.s.run([self.out], {"pixel_values": clip_pixels(img)})[0][0].astype(np.float32)
        return v / np.linalg.norm(v)


class ClipText:
    """CLIP text side (T4): a description -> a vector in the same space as ClipImage's."""

    def __init__(self, models: Path, threads: int = 0):
        from .clip_tokenizer import ClipTokenizer

        self.tok = ClipTokenizer(models / "clip" / "tokenizer.json")
        self.s = _session(models / "clip" / "text_model_quantized.onnx", threads)
        self.inputs = {i.name for i in self.s.get_inputs()}
        names = [o.name for o in self.s.get_outputs()]
        self.out = "text_embeds" if "text_embeds" in names else names[0]

    def embed(self, text: str) -> np.ndarray:
        ids = np.array([self.tok.encode(text)], np.int64)
        feed = {"input_ids": ids}
        if "attention_mask" in self.inputs:
            feed["attention_mask"] = np.ones_like(ids)
        v = self.s.run([self.out], feed)[0][0].astype(np.float32)
        return v / np.linalg.norm(v)


class RamTagger:
    def __init__(self, models: Path, threads: int = 0):
        d = models / "ram"
        self.s = _session(d / "vision_model_int8.onnx", threads)
        self.label_embed = np.load(d / "tag_embeddings.npy").astype(np.float32, copy=False)
        self.tags = (d / "ram_tag_list.txt").read_text(encoding="utf-8").splitlines()
        self.thresholds = np.loadtxt(d / "ram_tag_list_threshold.txt", dtype=np.float32)
        # RAM++ keeps several description embeddings per tag (51 x 4,585 rows); the model pools them.
        n = len(self.tags)
        if not n or n != len(self.thresholds) or len(self.label_embed) % n:
            raise StageError("model_invalid", "RAM++ tag list, thresholds and embeddings disagree")
        self.keep = np.array([t not in GENERIC_TAGS for t in self.tags])

    def tag(self, img: Image.Image, max_tags: int = MAX_TAGS) -> list[tuple[str, float]]:
        """Tags above RAM++'s per-tag threshold, strongest (relative to its threshold) first."""
        a = (np.asarray(img.resize((384, 384), Image.BILINEAR), np.float32) / 255).transpose(2, 0, 1)[None]
        scores = self.s.run(["tag_scores"], {"pixel_values": a, "label_embed": self.label_embed})[0][0]
        on = np.flatnonzero((scores > self.thresholds) & self.keep)
        on = on[np.argsort(-(scores[on] / self.thresholds[on]))][:max_tags]
        return [(self.tags[i], round(float(scores[i]), 4)) for i in on]
