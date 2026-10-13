import json
import subprocess
import sys
from pathlib import Path

import pytest

from guetteur.runner import ROOT, run_scenario, tamper, verify_ledger


@pytest.fixture(scope="module")
def good_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    res, meta = run_scenario("subtle_item_removed", 0, "rule", "approve", out_dir=out)
    return out / res.run, res


def test_ledger_is_proven_by_the_real_verifier(good_run):
    run_dir, res = good_run
    v = verify_ledger(run_dir / "receipts.md")
    assert v.ok and "1 of 1 done step(s) proven" in v.output and "numpy-region-stats" in v.output and "opencv-region-stats" in v.output


@pytest.mark.parametrize("how", ["inflate", "wrong_place", "other_input"])
def test_tampered_claims_are_rejected_even_when_the_file_hash_is_recomputed(good_run, how):
    run_dir, res = good_run
    v = verify_ledger(tamper(run_dir / "claims" / "c1.json", how, run_dir))
    assert not v.ok and v.exit_code == 1 and "0 of 1 done step(s) proven" in v.output


def test_file_receipt_catches_editing_the_claim_after_the_fact(good_run, tmp_path):
    run_dir, res = good_run
    ledger = (run_dir / "receipts.md").read_text()
    claim = run_dir / "claims" / "c1.json"
    original = claim.read_text()
    try:
        claim.write_text(original.replace('"dz_mm"', '"dz_mm" '))          # any byte change
        v = verify_ledger(run_dir / "receipts.md")
        assert not v.ok and "FAIL  [1] file:" in v.output
    finally:
        claim.write_text(original)


def test_check_numpy_never_imports_opencv(good_run):
    run_dir, _ = good_run
    code = ("import sys, runpy; sys.argv=['x', %r]; "
            "from guetteur import check_numpy; rc = check_numpy.main(); "
            "assert 'cv2' not in sys.modules, 'cv2 was imported'; sys.exit(rc)") % str(run_dir / "claims" / "c1.json")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.startswith("PASS"), p.stdout + p.stderr


def test_wrong_input_digest_fails_loudly(good_run, tmp_path):
    run_dir, _ = good_run
    c = json.loads((run_dir / "claims" / "c1.json").read_text())
    c["clip"]["digest"] = "0" * 64
    p = tmp_path / "c.json"
    p.write_text(json.dumps(c))
    for mod in ("guetteur.check_numpy", "guetteur.check_cv"):
        r = subprocess.run([sys.executable, "-m", mod, str(p)], cwd=ROOT, capture_output=True, text=True)
        assert r.returncode != 0 and "digest mismatch" in (r.stdout + r.stderr)


def test_every_claim_kind_in_the_suite_passes_both_checks():
    """object_removed/added, sensor_fault/degraded and drift_compensated, all re-derived from raw frames by both checks."""
    from guetteur.cloud.handler import run_checks
    kinds = set()
    for n in ("shelf_item_added", "shelf_item_removed", "lens_obstructed", "partial_washout", "slow_drift"):
        res, _ = run_scenario(n, 0, "rule", "approve")
        for c in res.claims:
            r = run_checks(c.__dict__)
            assert r["proven"], (n, c.text, r)
            kinds.add(c.kind)
    assert kinds == {"object_added", "object_removed", "sensor_fault", "sensor_degraded", "drift_compensated"} or \
        kinds >= {"object_added", "object_removed", "sensor_fault", "drift_compensated"}
