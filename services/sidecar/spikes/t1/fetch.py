"""Download the T1 inputs: CLIP + RAM++ ONNX models (Hugging Face) and a COCO val2017 sample."""

from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HF = "https://huggingface.co/{repo}/resolve/{rev}/{file}"
FILES = {
    "clip": ("Xenova/clip-vit-base-patch32", "6b7c523703ae26e39be4b03774966f8571c7f523",
             ["onnx/vision_model.onnx", "onnx/text_model.onnx", "onnx/vision_model_quantized.onnx",
              "onnx/text_model_quantized.onnx", "tokenizer.json"]),
    "ram": ("AX1Y2JP/recognize-anything-plus-model-ONNX", "aa4ee9c6e449e9dd8b20580a938d24b38dd5fcfb",
            ["onnx/vision_model_int8.onnx", "tag_embeddings.npy", "ram_tag_list.txt", "ram_tag_list_threshold.txt",
             "LICENSE"]),
}


def get(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def main(models: Path, coco: Path, n: int) -> None:
    for key, (repo, rev, files) in FILES.items():
        for f in files:
            # CLIP files are flattened into models/clip; RAM keeps its onnx/ folder
            dest = models / key / (Path(f).name if key == "clip" else f)
            get(HF.format(repo=repo, rev=rev, file=f), dest)
            print(key, f, dest.stat().st_size, hashlib.sha256(dest.read_bytes()).hexdigest(), flush=True)
    ann_path = coco / "instances_val2017.json"
    if not ann_path.exists():
        coco.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO(urllib.request.urlopen(
            "http://images.cocodataset.org/annotations/annotations_trainval2017.zip", timeout=900).read())
        ann_path.write_bytes(zipfile.ZipFile(buf).read("annotations/instances_val2017.json"))
    ann = json.loads(ann_path.read_text())
    imgs = {i["id"]: i for i in ann["images"]}
    sal = defaultdict(set)
    for a in ann["annotations"]:
        im = imgs[a["image_id"]]
        if a["area"] >= 0.02 * im["width"] * im["height"]:
            sal[a["image_id"]].add(a["category_id"])
    ids = sorted(sal)
    random.Random(1).shuffle(ids)  # same selection as t1_tagging.load_coco
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda i: get(imgs[i]["coco_url"], coco / "val2017" / imgs[i]["file_name"]), ids[:n]))
    print("coco images", len(list((coco / "val2017").iterdir())))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
