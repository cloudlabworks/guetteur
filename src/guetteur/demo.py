"""Narrated demo (the 'judge demo' without a camera). All scenes are SYNTHETIC depth and labelled as such."""

from __future__ import annotations

import json
from pathlib import Path

from .evalrun import score_run
from .runner import run_scenario, tamper, verify_ledger


def _hdr(s: str) -> None:
    print("\n" + "=" * 8 + " " + s + " " + "=" * max(3, 70 - len(s)))


def _steps(res, kinds=None) -> None:
    last = None
    for e in res.trace:
        a = e["action"]
        if a["kind"] == "continue" or (kinds and a["kind"] not in kinds):
            continue
        if a["kind"] == "pause_zone" and last == ("pause_zone", e["zone"]):
            continue                                   # a person lingering: one line is enough
        last = (a["kind"], e["zone"])
        print(f"  frame {e['frame']:>3} {e['zone']:<8} {a['kind']:<17} {json.dumps(a['args']) if a['args'] else '':<26}| {a['rationale'][:100]}")


def demo(out: str = "runs") -> int:
    print("Team Drift demo. Input: SYNTHETIC OAK-D-style depth frames (240x320, uint16 mm). No colour, no images, no camera.")

    _hdr("1. Ambiguous evidence changes the next tool call  (scenario: subtle_item_removed)")
    print("A low item (25 mm relief) is taken at frame 95. In ONE frame it sits at only ~3.4 sigma: not enough to bother a human.")
    res, meta = run_scenario("subtle_item_removed", 0, "rule", "approve", out_dir=out)
    _steps(res)
    print("  -> the planner saw contrast between 'ignore' and 'confident', so it called recheck_zone(n frames); the OpenCV 5 re-run on the")
    print("     integrated window decided whether a human is asked. Same scene, naive threshold-and-alert baseline:")
    nres, _ = run_scenario("subtle_item_removed", 0, "naive", "approve")
    print(f"     naive baseline alerts: {[(a['frame'], a['kind']) for a in nres.alerts] or 'none (change missed)'}")

    _hdr("2. Presence is not a change; a person takes an item  (scenario: person_takes_item)")
    res2, meta2 = run_scenario("person_takes_item", 0, "rule", "approve", out_dir=out)
    _steps(res2)
    print("  -> zone paused while the person stands at the shelf; alert only after they left, with the item gone.")

    _hdr("3. Human gate: no answer means no alert  (same scene, gate times out)")
    res3, _ = run_scenario("shelf_item_removed", 0, "rule", "timeout")
    _steps(res3, {"request_approval"})
    print(f"  alerts published: {res3.alerts or 'none'}  (default on timeout is deny)")

    _hdr("4. Sensor trouble is a different action, not a scene claim  (scenarios: noise_burst, slow_drift, lens_obstructed)")
    for n in ("noise_burst", "slow_drift", "lens_obstructed"):
        r, m = run_scenario(n, 0, "rule", "approve", out_dir=out)
        kinds = {}
        for e in r.trace:
            kinds[e["action"]["kind"]] = kinds.get(e["action"]["kind"], 0) + (e["action"]["kind"] != "continue")
        print(f"  {n:16s} actions {{ {', '.join(f'{k}: {v}' for k, v in kinds.items() if v)} }} alerts {[(a['kind']) for a in r.alerts] or 'none'}  success={score_run(r, m)['success']}")

    _hdr("5. Every claim carries a receipt: re-run by the prove-it-done verifier")
    led = Path(out) / res.run / "receipts.md"
    print(led.read_text().rstrip())
    v = verify_ledger(led)
    print("\n" + "\n".join(l for l in v.output.splitlines() if l.startswith(("PASS", "FAIL", "  ->", "summary"))))

    _hdr("6. Doctored claims are caught (tolerance is max(8 mm, 25%), so small inflations pass by design)")
    claim = Path(out) / res.run / "claims" / "c1.json"
    bad = 0
    for how in ("inflate", "wrong_place", "other_input"):
        tv = verify_ledger(tamper(claim, how, Path(out) / res.run))
        print(f"  tamper={how:12s} -> exit {tv.exit_code}  {tv.summary}")
        bad += tv.ok
    return 0 if v.ok and bad == 0 else 1
