"""`guetteur` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print_trace(res, only_actions: bool = True) -> None:
    for e in res.trace:
        a = e["action"]
        if only_actions and a["kind"] == "continue":
            continue
        print(f"  frame {e['frame']:>3} {e['zone']:<8} {a['kind']:<17} {json.dumps(a['args']) if a['args'] else '':<28} {a['rationale'][:90]}"
              + (f"  => {e['outcome']['status']}" if e["outcome"].get("status") != "ok" else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="guetteur", description="Team Drift: OpenCV 5 active-perception agent with receipts (synthetic/recorded depth only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scenarios", help="list synthetic scenarios")
    p = sub.add_parser("run", help="run one scenario through the agent loop")
    p.add_argument("scenario"); p.add_argument("--seed", type=int, default=0)
    p.add_argument("--planner", choices=["rule", "naive-guarded", "naive"], default="rule")
    p.add_argument("--gate", choices=["approve", "deny", "timeout"], default="approve")
    p.add_argument("--out", default="runs", help="directory for trace/claims/ledger ('' to skip)")
    p.add_argument("--verify", action="store_true", help="verify the receipt ledger with the prove-it-done verifier")
    p.add_argument("--all-steps", action="store_true", help="also print 'continue' steps")
    p = sub.add_parser("synth", help="write a synthetic clip (.npz of uint16 depth frames) to disk")
    p.add_argument("scenario"); p.add_argument("--seed", type=int, default=0); p.add_argument("--out", default="data/clips")
    p = sub.add_parser("verify-ledger", help="run the vendored prove-it-done verifier on a receipt ledger")
    p.add_argument("ledger")
    p = sub.add_parser("eval", help="run every scenario x seeds x {rule,naive}; write eval/results.json")
    p.add_argument("--seeds", type=int, default=3); p.add_argument("--out", default="eval"); p.add_argument("--no-verify", action="store_true")
    p = sub.add_parser("bench", help="per-frame runtime on this machine; writes eval/runtime.json")
    p.add_argument("--out", default="eval/runtime.json")
    p = sub.add_parser("demo", help="narrated 2-minute demo: ambiguous evidence -> recheck -> approval -> receipts -> tamper")
    p.add_argument("--out", default="runs")
    p = sub.add_parser("serve", help="serve the judge dashboard + API locally (same handler as the Lambda)")
    p.add_argument("--port", type=int, default=8080)
    a = ap.parse_args(argv)

    if a.cmd == "scenarios":
        from . import synth
        for n, s in synth.scenarios().items():
            print(f"{n:28s} {s.note}")
        return 0
    if a.cmd == "run":
        from .evalrun import score_run
        from .runner import run_scenario, verify_ledger
        res, meta = run_scenario(a.scenario, a.seed, a.planner, a.gate, out_dir=a.out or None)
        print(f"SYNTHETIC scene '{a.scenario}' seed {a.seed}: {meta['note']}")
        _print_trace(res, not a.all_steps)
        print("counters:", {k: v for k, v in res.counters.items() if v})
        print("alerts published:", [(x["frame"], x["kind"], x["zone"]) for x in res.alerts] or "none")
        print("score vs ground truth:", {k: v for k, v in score_run(res, meta).items() if k != "latency_frames"})
        if a.verify and a.out and res.claims:
            v = verify_ledger(Path(a.out) / res.run / "receipts.md")
            print(v.output.rstrip())
            return v.exit_code
        return 0
    if a.cmd == "synth":
        from . import synth
        print(synth.make_clip(a.scenario, a.seed, a.out))
        return 0
    if a.cmd == "verify-ledger":
        from .runner import verify_ledger
        v = verify_ledger(a.ledger)
        print(v.output.rstrip())
        return v.exit_code
    if a.cmd == "eval":
        from .evalrun import evaluate
        r = evaluate(a.seeds, a.out, verify=not a.no_verify)
        for pl, d in r["planners"].items():
            print(f"{pl}: success {d['scenario_success']}/{d['runs']} precision {d['precision']:.2f} recall {d['recall']:.2f} "
                  f"(recall excl. known miss {d['recall_excl_known_miss']:.2f}) tamper {d['tamper_rejected']} receipts {d['receipts_verified']}")
        return 0
    if a.cmd == "bench":
        from .evalrun import benchmark
        r = benchmark()
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(r, indent=2))
        print(json.dumps({k: v for k, v in r.items() if k != "stages"}, indent=2))
        for k, v in r["stages"].items():
            print(f"  {k:32s} median {v['median_ms']:.3f} ms  p95 {v['p95_ms']:.3f} ms")
        return 0
    if a.cmd == "demo":
        from .demo import demo
        return demo(a.out)
    if a.cmd == "serve":
        from .cloud.local import serve
        serve(a.port)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
