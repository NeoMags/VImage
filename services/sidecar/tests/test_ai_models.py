"""T2: the real CLIP and RAM++ models on a real photo.

Needs the downloaded models (IV_MODELS_DIR) and a test photo (IV_TEST_PHOTO: COCO val2017
000000039769.jpg, two cats on a couch). CI provides both and sets IV_REQUIRE_MODELS=1, so
there these tests always run; elsewhere they are skipped when the files are absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ivsidecar import ai_models
from ivsidecar.stages.ai import ClipImage, RamTagger

MODELS = Path(os.environ.get("IV_MODELS_DIR", "/nonexistent"))
PHOTO = Path(os.environ.get("IV_TEST_PHOTO", "/nonexistent"))
READY = ai_models.installed(Path("/unused")) if "IV_MODELS_DIR" in os.environ else False

if not (READY and PHOTO.is_file()):
    if os.environ.get("IV_REQUIRE_MODELS") == "1":
        raise RuntimeError(f"models or test photo missing: {MODELS} {PHOTO}")
    pytest.skip("AI models not downloaded here", allow_module_level=True)


@pytest.fixture(scope="module")
def photo() -> Image.Image:
    img = Image.open(PHOTO).convert("RGB")
    img.thumbnail((512, 512))  # what the stage reads: the 512 px thumbnail
    return img


def test_ram_tags_the_cats(photo):
    tags = RamTagger(MODELS, threads=2).tag(photo)
    names = [t for t, _ in tags]
    assert "cat" in names, tags
    assert 1 <= len(tags) <= 8
    assert all(0 < s <= 1 for _, s in tags)
    assert not {"image", "photo"} & set(names)


def test_clip_embedding_is_stable_and_separates_unlike_images(photo):
    clip = ClipImage(MODELS, threads=2)
    a = clip.embed(photo)
    assert a.shape == (512,) and np.isclose(np.linalg.norm(a), 1, atol=1e-4)
    assert float(a @ clip.embed(photo.transpose(Image.Transpose.FLIP_LEFT_RIGHT))) > 0.9  # same scene, mirrored
    other = Image.new("RGB", (512, 384), (20, 90, 200))  # a flat blue picture
    assert float(a @ clip.embed(other)) < 0.8
