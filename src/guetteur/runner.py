"""Run a scenario end to end: render -> agent loop -> trace, claims, ledger on disk -> optional verification."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import synth
from .agent import Agent, HumanGate, RunResult, ScriptedGate
from .common import depth_digest, save_clip
from .perception import PerceptionParams
from .planner import NaivePlanner, Planner, PlannerConfig, RulePlanner
from .receipts import Claim, ledger_text, sha256_file, write_claims

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "third_party" / "prove_it_done" / "verify_receipts.py"
PLANNERS = {"rule": RulePlanner, "naive": NaivePlanner, "naive-guarded": NaivePlanner}


def make_planner(name: str) -> Planner:
    return PLANNERS[name]()


def run_scenario(name: str, seed: int = 0, planner: str | Planner = "rule", gate: HumanGate | str = "approve",
                 out_dir: str | Path | None = None, keep_clip: bool = False) -> tuple[RunResult, dict]:
    """Returns (result, meta). With out_dir, writes trace.jsonl, claims/, receipts.md, summary.json under out_dir/<run>/."""
    sc = synth.scenarios()[name]
    depth = synth.render(sc, seed)
    digest = depth_digest(depth)
    pl = make_planner(planner) if isinstance(planner, str) else planner
    g = ScriptedGate(gate) if isinstance(gate, str) else gate
    run_id = f"{name}_s{seed}_{planner if isinstance(planner, str) else getattr(pl, 'name', 'planner')}"
    clip_path = None
    if out_dir is not None and keep_clip:
        clip_path = str((Path(out_dir) / run_id / "clip.npz"))
        save_clip(clip_path, depth, dict(synthetic=True, scenario=name, seed=seed))
    clip_ref = dict(path=_rel(clip_path), scenario=name, seed=seed, digest=digest)
    res = Agent(pl, g, guardrails=planner != "naive").run(depth, run_id, clip_ref)
    meta = dict(scenario=name, seed=seed, run=run_id, digest=digest, expect=sc.expect, truth=sc.truth, note=sc.note)
    if out_dir is not None:
        write_run(res, meta, Path(out_dir) / run_id)
    return res, meta


def _rel(p: str | None) -> str | None:
    if p is None:
        return None
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def write_run(res: RunResult, meta: dict, run_dir: Path) -> Path | None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "trace.jsonl", "w") as fh:
        for rec in res.trace:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(dict(meta=meta, counters=res.counters, requests=res.requests, alerts=res.alerts), indent=2, default=str))
    if not res.claims:
        return None
    paths = write_claims(res.claims, run_dir)
    rel_paths = [Path(_rel(str(p))) for p in paths]
    ledger = run_dir / "receipts.md"
    ledger.write_text(ledger_text(res.claims, rel_paths, res.run))
    return ledger


@dataclass
class Verdict:
    ok: bool
    exit_code: int
    summary: str
    output: str


def verify_ledger(ledger: str | Path, min_instruments: int = 2, timeout: int = 300) -> Verdict:
    """Run the vendored prove-it-done verifier on a ledger, from the repo root, with this interpreter first on PATH."""
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    p = subprocess.run([sys.executable, str(VERIFIER), "--ledger", str(_rel(str(ledger))), "--min-instruments", str(min_instruments)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    out = p.stdout + p.stderr
    m = re.findall(r"^summary:.*$", out, re.M)
    return Verdict(p.returncode == 0, p.returncode, m[-1] if m else "(no summary line)", out)


# ----------------------------------------------------------------------------- tampering (negative tests)

def tamper(claim_path: str | Path, how: str, out_dir: str | Path) -> Path:
    """Write a tampered copy of a claim plus a ledger whose `file:` hash is RECOMPUTED, so that only the two
    behavioural checks can catch the lie. Returns the ledger path."""
    c = json.loads(Path(claim_path).read_text())
    if how == "inflate":                       # claim a bigger effect than the input shows
        a = c["asserted"]
        if "dz_mm" in a:
            a["dz_mm"] = a["dz_mm"] * 2.0
        elif "valid_frac" in a:
            a["valid_frac"] = min(0.95, a["valid_frac"] + 0.35)
        else:
            a["gain"] = a["gain"] + 0.05
    elif how == "wrong_place":                 # right words, wrong pixels (object claims) or wrong time (sensor/drift claims)
        if c["kind"] == "object_removed" or c["kind"] == "object_added":
            c["region"] = [150, 190, 210, 250]
        else:                                  # a whole-frame fault is true anywhere in the frame, so move it in time instead
            c["evidence"] = [10, 18]
    elif how == "other_input":                 # claim silently re-pointed at a different recording
        c["clip"]["seed"] += 1
        c["clip"]["path"] = None
    else:
        raise ValueError(how)
    out = Path(out_dir) / f"tamper_{how}"
    (out / "claims").mkdir(parents=True, exist_ok=True)
    p = out / "claims" / f"{c['id']}.json"
    p.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")
    rel = Path(_rel(str(p))).as_posix()
    led = out / "receipts.md"
    led.write_text("\n".join([
        f"# Tampered ({how})", "", f"- [x] 1. {c['id']}: {c.get('text', '')}",
        f"  - receipt: `file:{rel} | sha256={sha256_file(p)}` ",
        f"  - receipt: `cmd:python -m guetteur.check_numpy {rel} | contains=PASS | via=numpy-region-stats`",
        f"  - receipt: `cmd:python -m guetteur.check_cv {rel} | contains=PASS | via=opencv-region-stats`", ""]))
    return led
