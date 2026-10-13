"""Receipt check #1: numpy only (this module must never import cv2). Re-derives a claim from the raw clip.

    python -m guetteur.check_numpy runs/<run>/claims/c1.json      -> prints PASS/FAIL, exit 0/1 (2 = error)
"""

from __future__ import annotations

import json
import sys

import numpy as np

from .common import depth_digest, load_clip


def load_depth(claim: dict) -> np.ndarray:
    """Clip file if present, else deterministic regeneration from (scenario, seed); either way the digest must match."""
    clip = claim["clip"]
    depth = None
    if clip.get("path"):
        try:
            depth = load_clip(clip["path"]).depth
        except (FileNotFoundError, OSError):
            depth = None
    if depth is None:
        from . import synth     # numpy-only module
        depth = synth.render(synth.scenarios()[clip["scenario"]], clip["seed"])
    got = depth_digest(depth)
    if got != clip["digest"]:
        raise SystemExit(f"FAIL clip digest mismatch: claim pins {clip['digest'][:12]}, input is {got[:12]}")
    return depth


def region(depth: np.ndarray, claim: dict, window: list[int]) -> np.ndarray:
    r0, r1, c0, c1 = claim["region"]
    return depth[window[0]:window[1], r0:r1, c0:c1].astype(np.float32)


def _pixel_medians(stack: np.ndarray) -> np.ndarray:
    return np.nanmedian(np.where(stack > 0, stack, np.nan), axis=0)


def check(claim: dict, depth: np.ndarray) -> tuple[bool, str]:
    kind, a, tol = claim["kind"], claim["asserted"], claim["tol"]
    base, ev = region(depth, claim, claim["baseline"]), region(depth, claim, claim["evidence"])
    if kind in ("object_removed", "object_added"):
        bm, em = _pixel_medians(base), _pixel_medians(ev)
        ok_px = np.isfinite(bm) & np.isfinite(em)
        if ok_px.sum() < 50:
            return False, "not enough valid pixels in the claimed region"
        d = em[ok_px] - bm[ok_px]
        measured = float(np.median(d))
        want = a["dz_mm"]
        same = float(np.mean(np.sign(d) == np.sign(want)))
        good = (np.sign(measured) == np.sign(want) and abs(measured) >= 15.0
                and abs(measured - want) <= max(tol["abs_mm"], tol["rel"] * abs(want)) and same >= 0.6)
        return good, f"{kind}: measured {measured:+.0f} mm vs asserted {want:+.0f} mm; {same:.0%} of {ok_px.sum()} region pixels moved that way"
    if kind in ("sensor_fault", "sensor_degraded"):
        vb, ve = float((base > 0).mean()), float((ev > 0).mean())
        good = vb >= 0.9 and abs(ve - a["valid_frac"]) <= tol["valid_abs"] and ve < 0.85
        return good, f"{kind}: valid fraction {ve:.2f} (asserted {a['valid_frac']:.2f}) vs {vb:.2f} at baseline"
    if kind == "drift_compensated":
        bm, em = _pixel_medians(base), _pixel_medians(ev)
        ok_px = np.isfinite(bm) & np.isfinite(em)
        measured = float(np.median(em[ok_px] / bm[ok_px])) - 1.0
        good = abs(measured - a["gain"]) <= tol["gain_abs"]
        return good, f"{kind}: whole-region depth ratio {measured:+.4f} vs asserted {a['gain']:+.4f}"
    return False, f"unknown claim kind {kind!r}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m guetteur.check_numpy CLAIM.json", file=sys.stderr)
        return 2
    claim = json.load(open(argv[0]))
    ok, msg = check(claim, load_depth(claim))
    print(("PASS " if ok else "FAIL ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
