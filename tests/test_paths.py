import os

import pytest

from guetteur.common import safe_child
from guetteur.receipts import Claim, write_claims
from guetteur.runner import run_scenario


def test_safe_child_keeps_normal_paths(tmp_path):
    p = safe_child(tmp_path, "run_s0_rule", "claims")
    assert p == tmp_path.resolve() / "run_s0_rule" / "claims"


@pytest.mark.parametrize("part", ["../escape", "a/../../escape", "/etc/passwd", ".", ""])
def test_safe_child_rejects_escapes(tmp_path, part):
    with pytest.raises(ValueError):
        safe_child(tmp_path / "base", part)


def test_safe_child_rejects_symlink_out(tmp_path):
    base, outside = tmp_path / "base", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    os.symlink(outside, base / "link")
    with pytest.raises(ValueError):
        safe_child(base, "link", "x.json")


def test_write_claims_rejects_traversing_claim_id(tmp_path):
    c = Claim("../../evil", "r", "object_removed", "shelf", [0, 1, 0, 1], [0, 1], [0, 1], {"dz_mm": 1.0}, {}, 0, "t")
    with pytest.raises(ValueError):
        write_claims([c], tmp_path / "run")
    assert not (tmp_path / "evil.json").exists()


def test_run_scenario_rejects_unknown_names(tmp_path):
    with pytest.raises(KeyError):
        run_scenario("../../escape", 0, "rule", "approve", out_dir=tmp_path)
    with pytest.raises(KeyError):
        run_scenario("shelf_item_removed", 0, "../rule", "approve", out_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_run_scenario_writes_under_out_dir_with_same_run_id(tmp_path):
    res, meta = run_scenario("shelf_item_removed", 0, "naive-guarded", "approve", out_dir=tmp_path, keep_clip=True)
    assert meta["run"] == res.run == "shelf_item_removed_s0_naive-guarded"
    run_dir = tmp_path / meta["run"]
    assert (run_dir / "summary.json").is_file() and (run_dir / "clip.npz").is_file()
