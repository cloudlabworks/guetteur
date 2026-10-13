import subprocess
import sys
from pathlib import Path

import numpy as np

from guetteur import synth
from guetteur.common import CALIB_FRAMES, H, W, depth_digest, load_clip, save_clip

ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".mp4", ".avi", ".mov"}


def test_scenes_are_deterministic_and_labelled(tmp_path):
    sc = synth.scenarios()["shelf_item_removed"]
    a, b = synth.render(sc, 3), synth.render(sc, 3)
    assert a.shape == (sc.T, H, W) and a.dtype == np.uint16
    assert depth_digest(a) == depth_digest(b)
    assert depth_digest(a) != depth_digest(synth.render(sc, 4))
    p = synth.make_clip("shelf_item_removed", 3, str(tmp_path))
    clip = load_clip(p)
    assert clip.meta["synthetic"] is True and "SYNTHETIC" in clip.meta["label"]
    assert clip.digest == clip.meta["digest"] == depth_digest(a)


def test_noise_model_roughly_matches_stereo_physics():
    d = synth.render(synth.scenarios()["empty"], 0)[:CALIB_FRAMES]
    shelf = d[:, 100:110, 100:110].astype(np.float32)
    v = shelf[shelf > 0]
    assert 1700 < np.median(v) < 1900
    assert 15 < np.std(v) < 40            # sigma ~ 0.8e-5 * 1800^2 = 26 mm


def test_no_image_or_video_files_in_code_data_or_run_outputs(tmp_path):
    """Run outputs and every code/data folder are picture-free. (docs/ may hold documentation graphics for the
    submission gallery; those are not produced by, or read by, any code path.)"""
    from guetteur.runner import run_scenario
    run_scenario("shelf_item_removed", 0, "rule", "approve", out_dir=tmp_path)
    skip = {".venv", ".git", "docs", "runs", "__pycache__", ".pytest_cache"}
    for root in (ROOT, tmp_path):
        for p in root.rglob("*"):
            rel = p.relative_to(root).parts
            if rel and rel[0] in skip and root == ROOT:
                continue
            assert p.suffix.lower() not in IMAGE_EXT, f"image/video file found: {p}"


def test_no_code_path_writes_pictures():
    banned = ("imwrite", "imencode", "VideoWriter", "savefig", "PIL", "imshow")
    for p in (ROOT / "src").rglob("*.py"):
        text = p.read_text()
        for b in banned:
            assert b not in text, f"{p} mentions {b}"


def test_api_rejects_pictures_and_oversize():
    import pytest
    from guetteur.cloud.store import EvidenceRejected, validate_evidence
    ok = {"claim": {"id": "c1"}, "summary": "x", "grid": [[0] * 8] * 6}
    assert validate_evidence(ok) is ok
    with pytest.raises(EvidenceRejected):
        validate_evidence({"claim": {}, "image": "AAAA"})
    with pytest.raises(EvidenceRejected):
        validate_evidence({"claim": {}, "nested": {"depth_map": [1]}})
    with pytest.raises(EvidenceRejected):
        validate_evidence({"claim": {}, "blob": "x" * 20000})
    with pytest.raises(EvidenceRejected):
        validate_evidence({"claim": {}, "rows": list(range(500))})
    with pytest.raises(EvidenceRejected):
        validate_evidence({"no": "claim"})
