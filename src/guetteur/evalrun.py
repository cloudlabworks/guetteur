"""Evaluation and benchmark harness. Everything it measures is on SYNTHETIC scenes with machine-readable truth."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from . import synth
from .common import CALIB_FRAMES, F_PX, FPS, H, W, ZONES
from .runner import ROOT, run_scenario, tamper, verify_ledger

FAMILY = {"object_removed": "object_change", "object_added": "object_change",
          "sensor_fault": "sensor_fault", "sensor_degraded": "sensor_fault"}


def score_run(res, meta: dict) -> dict:
    """Event-level scoring of the agent's approval REQUESTS against the scenario's expected escalations."""
    expect = meta["expect"]
    exp = [dict(e) for e in expect.get("escalations", [])]
    reqs = [(r["frame"], FAMILY[r["request"]["kind"]], r["request"]["zone"]) for r in res.requests]
    used, tp, lat = set(), 0, []
    for e in exp:
        for i, (f, fam, zone) in enumerate(reqs):
            if i in used or fam != e["kind"] or (e["zone"] != "*" and zone != e["zone"]):
                continue
            if f >= e.get("not_before", e["t0"]) and f <= e["by"]:
                used.add(i)
                tp += 1
                lat.append(f - e["t0"])
                break
    fn = len(exp) - tp
    fp = len(reqs) - len(used)
    c = res.counters
    needs = dict(min_rechecks="rechecks", min_pauses="pauses", min_compensations="compensations")
    missing = [k for k, ck in needs.items() if expect.get(k, 0) > c.get(ck, 0)]
    # for sensor faults several zones may ask; the extra asks count as duplicates, not false alarms
    return dict(tp=tp, fp=fp, fn=fn, latency_frames=lat, behaviours_missing=missing,
                success=(fn == 0 and fp == 0 and not missing), known_miss=bool(expect.get("known_miss")))


def _pct(a: list[float], q: float) -> float:
    return float(np.percentile(a, q)) if a else 0.0


def env_info() -> dict:
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip() or platform.processor()
    return dict(cpu=cpu, machine=platform.machine(), system=f"{platform.system()} {platform.release()}", python=platform.python_version(),
                opencv=cv2.__version__, opencv_pip_pin="opencv-python-headless==5.0.0.93", numpy=np.__version__,
                cv2_threads=cv2.getNumThreads(), cv2_optimized=cv2.useOptimized(), frame=f"{W}x{H} uint16 depth",
                cuda="none (Apple Silicon, CPU only)")


def evaluate(seeds: int = 3, out_dir: str | Path = "eval", verify: bool = True, log=print) -> dict:
    out = Path(out_dir)
    runs_dir = out / "runs"
    shutil.rmtree(runs_dir, ignore_errors=True)
    names = list(synth.scenarios())
    results: dict = {"env": env_info(), "seeds": seeds, "planners": {}}
    for planner in ("rule", "naive-guarded", "naive"):
        rows, per_ms, pl_ms, fr_ms = [], [], [], []
        for name in names:
            for seed in range(seeds):
                res, meta = run_scenario(name, seed, planner, "approve", out_dir=runs_dir)
                s = score_run(res, meta)
                row = dict(scenario=name, seed=seed, run=res.run, **s, counters=res.counters, n_claims=len(res.claims),
                           alerts=[(a["frame"], a["kind"], a["zone"]) for a in res.alerts])
                if verify and planner == "rule" and res.claims:
                    v = verify_ledger(runs_dir / res.run / "receipts.md")
                    row["receipts"] = dict(ok=v.ok, summary=v.summary)
                    if seed == 0:
                        row["tamper"] = {}
                        for how in ("inflate", "wrong_place", "other_input"):
                            for c in res.claims[:2]:
                                led = tamper(runs_dir / res.run / "claims" / f"{c.id}.json", how, runs_dir / res.run / f"t_{c.id}")
                                tv = verify_ledger(led)
                                row["tamper"].setdefault(how, []).append(dict(claim=c.id, rejected=not tv.ok, exit=tv.exit_code))
                rows.append(row)
                per_ms += res.perception_ms; pl_ms += res.planner_ms; fr_ms += res.frame_ms
                log(f"{planner:5s} {name:28s} s{seed} success={s['success']!s:5s} tp={s['tp']} fp={s['fp']} fn={s['fn']} "
                    f"claims={len(res.claims)}" + (f" receipts_ok={row['receipts']['ok']}" if "receipts" in row else ""))
        tp = sum(r["tp"] for r in rows); fp = sum(r["fp"] for r in rows); fn = sum(r["fn"] for r in rows)
        known = sum(r["fn"] for r in rows if r["known_miss"])
        lat = [x for r in rows for x in r["latency_frames"]]
        tam = [t for r in rows for lst in r.get("tamper", {}).values() for t in lst]
        rec = [r["receipts"]["ok"] for r in rows if "receipts" in r]
        pc = sum(r["counters"]["planner_calls"] for r in rows); fr = sum(r["counters"]["frames"] for r in rows)
        results["planners"][planner] = dict(
            runs=len(rows), scenario_success=sum(r["success"] for r in rows), scenario_success_excl_known_miss=
            sum(r["success"] for r in rows if not r["known_miss"]), n_excl_known_miss=sum(not r["known_miss"] for r in rows),
            tp=tp, fp=fp, fn=fn, fn_known_miss=known,
            precision=tp / (tp + fp) if tp + fp else 1.0, recall=tp / (tp + fn) if tp + fn else 1.0,
            recall_excl_known_miss=tp / (tp + fn - known) if tp + fn - known else 1.0,
            median_latency_frames=float(np.median(lat)) if lat else None, latency_s_at_fps=float(np.median(lat)) / FPS if lat else None,
            receipts_verified=dict(ledgers=len(rec), all_proven=sum(rec)) if rec else None,
            tamper_rejected=dict(attempts=len(tam), rejected=sum(t["rejected"] for t in tam)) if tam else None,
            planner_calls=pc, frames=fr, planner_call_rate=pc / fr if fr else 0.0,
            rows=rows,
            runtime_ms=dict(perception_per_zone_frame=dict(n=len(per_ms), median=float(np.median(per_ms)), p95=_pct(per_ms, 95), p99=_pct(per_ms, 99)),
                            planner_per_call=dict(n=len(pl_ms), median=float(np.median(pl_ms)) if pl_ms else 0.0, p95=_pct(pl_ms, 95)),
                            loop_per_frame_all_zones=dict(n=len(fr_ms), median=float(np.median(fr_ms)), p95=_pct(fr_ms, 95), p99=_pct(fr_ms, 99))))
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    return results


def stage_bench(reps: int = 200) -> dict:
    """Time each OpenCV 5 call on a representative 168x144 zone crop (shelf) so the Graviton comparison is per stage."""
    rng = np.random.default_rng(0)
    z = (1800 + rng.normal(0, 8, (168, 144))).astype(np.uint16)
    z[rng.random(z.shape) < 0.03] = 0
    inv = (z == 0).astype(np.uint8) * 255
    a8, b8 = (z // 16).astype(np.uint8), np.roll((z // 16).astype(np.uint8), 2, 1)
    mask = (rng.random(z.shape) > 0.9).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    zf = rng.normal(0, 1, z.shape).astype(np.float32)
    stages = {
        "inpaint_TELEA_16U": lambda: cv2.inpaint(z, inv, 3, cv2.INPAINT_TELEA),
        "medianBlur_5_16U": lambda: cv2.medianBlur(z, 5),
        "morphologyEx_open_3x3": lambda: cv2.morphologyEx(mask, cv2.MORPH_OPEN, k),
        "connectedComponentsWithStats": lambda: cv2.connectedComponentsWithStats(mask, connectivity=8),
        "calcOpticalFlowFarneback": lambda: cv2.calcOpticalFlowFarneback(a8, b8, None, 0.5, 2, 11, 2, 5, 1.1, 0),
        "resize_INTER_AREA_6x8": lambda: cv2.resize(zf, (8, 6), interpolation=cv2.INTER_AREA),
    }
    out = {}
    for name, fn in stages.items():
        for _ in range(10):
            fn()
        ts = []
        for _ in range(reps):
            t0 = time.perf_counter(); fn(); ts.append((time.perf_counter() - t0) * 1000)
        out[name] = dict(median_ms=round(float(np.median(ts)), 4), p95_ms=round(_pct(ts, 95), 4))
    return out


def benchmark(frames_scenarios=("shelf_item_removed", "person_takes_item", "slow_drift"), seeds: int = 3) -> dict:
    per, pl, fr = [], [], []
    for n in frames_scenarios:
        for s in range(seeds):
            res, _ = run_scenario(n, s, "rule", "approve")
            per += res.perception_ms; pl += res.planner_ms; fr += res.frame_ms
    return dict(env=env_info(),
                per_zone_frame_ms=dict(n=len(per), median=round(float(np.median(per)), 3), p95=round(_pct(per, 95), 3), p99=round(_pct(per, 99), 3)),
                planner_call_ms=dict(n=len(pl), median=round(float(np.median(pl)), 4), p95=round(_pct(pl, 95), 4)),
                loop_frame_ms_2_zones=dict(n=len(fr), median=round(float(np.median(fr)), 3), p95=round(_pct(fr, 95), 3), p99=round(_pct(fr, 99), 3)),
                stages=stage_bench(),
                note=f"analysis rate in the loop is {FPS} fps; the budget is {1000 // FPS} ms per frame")
