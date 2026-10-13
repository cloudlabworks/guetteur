"""Receipt check #2: OpenCV 5, a different estimator from both the live pipeline and check_numpy.

The live pipeline works per pixel on sigma-normalised, inpainted, median-filtered differences and connected
components. This check uses per-FRAME region statistics (cv2.mean with a validity mask, cv2.meanStdDev for the
standard error, cv2.countNonZero for coverage). It shares only the raw input with the other two.

    python -m guetteur.check_cv runs/<run>/claims/c1.json
"""

from __future__ import annotations

import json
import sys

import cv2
import numpy as np

from .check_numpy import load_depth


def _frame_means(depth: np.ndarray, region: list[int], window: list[int]) -> np.ndarray:
    r0, r1, c0, c1 = region
    out = []
    for f in depth[window[0]:window[1]]:
        crop = np.ascontiguousarray(f[r0:r1, c0:c1])
        mask = (crop > 0).astype(np.uint8)
        out.append(cv2.mean(crop.astype(np.float32), mask=mask)[0])
    return np.array(out, dtype=np.float64)


def _frame_cover(depth: np.ndarray, region: list[int], window: list[int]) -> np.ndarray:
    r0, r1, c0, c1 = region
    area = (r1 - r0) * (c1 - c0)
    return np.array([cv2.countNonZero(np.ascontiguousarray(f[r0:r1, c0:c1])) / area for f in depth[window[0]:window[1]]])


def check(claim: dict, depth: np.ndarray) -> tuple[bool, str]:
    kind, a, tol = claim["kind"], claim["asserted"], claim["tol"]
    region = claim["region"]
    if kind in ("object_removed", "object_added", "drift_compensated"):
        mb, me = _frame_means(depth, region, claim["baseline"]), _frame_means(depth, region, claim["evidence"])
        if len(mb) < 5 or len(me) < 3 or (mb <= 0).any() or (me <= 0).any():
            return False, "not enough usable frames in the claimed windows"
        _, sb = cv2.meanStdDev(mb.reshape(-1, 1))
        _, se = cv2.meanStdDev(me.reshape(-1, 1))
        d = float(me.mean() - mb.mean())
        stderr = float(np.hypot(sb[0, 0] / np.sqrt(len(mb)), se[0, 0] / np.sqrt(len(me))))
        if kind == "drift_compensated":
            ratio = float(me.mean() / mb.mean()) - 1.0
            return abs(ratio - a["gain"]) <= tol["gain_abs"], f"{kind}: frame-mean depth ratio {ratio:+.4f} vs asserted {a['gain']:+.4f}"
        want = a["dz_mm"]
        good = (np.sign(d) == np.sign(want) and abs(d) >= 4.0 * stderr and abs(d) >= 15.0
                and abs(d - want) <= max(tol["abs_mm"], tol["rel"] * abs(want)))
        return bool(good), f"{kind}: frame-mean shift {d:+.0f} mm (standard error {stderr:.1f}) vs asserted {want:+.0f} mm"
    if kind in ("sensor_fault", "sensor_degraded"):
        vb, ve = float(_frame_cover(depth, region, claim["baseline"]).mean()), float(_frame_cover(depth, region, claim["evidence"]).mean())
        good = vb >= 0.9 and abs(ve - a["valid_frac"]) <= tol["valid_abs"] and ve < 0.85
        return bool(good), f"{kind}: cv2.countNonZero coverage {ve:.2f} (asserted {a['valid_frac']:.2f}) vs {vb:.2f} at baseline"
    return False, f"unknown claim kind {kind!r}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m guetteur.check_cv CLAIM.json", file=sys.stderr)
        return 2
    claim = json.load(open(argv[0]))
    ok, msg = check(claim, load_depth(claim))
    print(("PASS " if ok else "FAIL ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
