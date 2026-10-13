# Guetteur (Team Drift): active perception with receipts

*Guetteur* is French for "lookout". An agent watches a space through **depth only**, decides what to look at next
using **OpenCV 5** evidence, stops at a **human gate** before anything outward-facing happens, and attaches a
**re-runnable receipt** to every claim it makes.

> **Status: local prototype, nothing deployed.** Every number in this repo comes from **synthetic** OAK-D-style depth
> scenes (labelled as such, with machine-readable ground truth). No physical camera was used yet; no AWS account
> was touched. See `DESIGN.md` for the plan and `docs/EVALUATION.md` for results and failure cases.
>
> Built for the OpenCV AI Competition 2026 (Agentic Vision path). Rules notes with section citations: `docs/RULES_NOTES.md`.

## What it does

```
depth frames ──> OpenCV 5 perception ──> evidence (JSON, no pixels) ──> planner ──> action ──> next perception call
                 inpaint, medianBlur,                                  rule-based    recheck_zone / pause_zone /
                 morphology, connected                                 (LLM slot)    compensate_drift / degrade_zone
                 components, Farneback                                               request_approval  (human gate)
                                                                                          │
                                           every claim ──> claim.json ──> receipts ──> prove-it-done verifier
```

* **Ambiguous evidence changes the next tool call.** A 25 mm change is ~3.5 sigma in one frame; the planner asks
  `recheck_zone(n=7)` and OpenCV 5 re-runs on the integrated window before anyone is bothered.
* **Presence is not a change.** A person-sized blob nearer than the scene pauses that zone.
* **Sensor trouble is not a scene event.** Noise bursts degrade the zone, gain drift is compensated, lost depth escalates as a camera fault.
* **Default is deny.** If no human answers the approval request, nothing is published.
* **Claims carry receipts.** Each claim is re-checked by two independent estimators (numpy-only, and OpenCV frame statistics) against the pinned input, and the vendored [prove-it-done](https://github.com/issaquahd/prove-it-done) verifier judges the ledger. Tampered claims fail.

## Quick start (macOS/Linux, Python 3.12, [uv](https://docs.astral.sh/uv/))

```bash
uv sync                                    # pinned: opencv-python-headless==5.0.0.93, numpy==2.5.4, pytest==9.1.1 (see uv.lock)
uv run pytest                              # ~2-3 minutes; includes the real verifier and tamper tests
uv run guetteur scenarios                  # the 11 synthetic scenarios and their ground truth
uv run guetteur demo                       # narrated demo (the judge demo without a camera)
uv run guetteur run subtle_item_removed --verify     # one scenario: trace, alerts, score, receipts
uv run guetteur run person_takes_item --gate timeout # human gate times out -> nothing is published
uv run guetteur run subtle_item_removed --planner naive   # the threshold-and-alert baseline misses it
uv run guetteur eval                       # all scenarios x 3 seeds x 3 planners -> eval/results.json
uv run guetteur bench                      # per-frame runtime on this machine -> eval/runtime.json
uv run guetteur serve                      # the judge dashboard + API on http://127.0.0.1:8080/
```

Re-verify any ledger yourself: `uv run guetteur verify-ledger runs/<run>/receipts.md` (runs the vendored
`third_party/prove_it_done/verify_receipts.py`, pinned to prove-it-done commit `d897534`).

## Layout

| Path | What |
| --- | --- |
| `src/guetteur/perception.py` | OpenCV 5 pipeline: holes, denoise, change evidence, morphology, blobs, optical flow, coarse grid |
| `src/guetteur/planner.py` | `Planner` interface, `RulePlanner`, `NaivePlanner` (ablation), `LLMPlanner` slot + action validation |
| `src/guetteur/agent.py` | executor: autonomy policy, evidence guardrails, human gate, trace |
| `src/guetteur/receipts.py`, `check_numpy.py`, `check_cv.py` | claims, ledger writer, the two independent re-checks |
| `src/guetteur/synth.py` | synthetic depth scenes with ground truth (11 scenarios) |
| `src/guetteur/cloud/` | the Lambda handler (also served locally), dashboard page, approval/evidence store |
| `INFRA/` | AWS SAM template **draft** (not deployed) |
| `third_party/prove_it_done/` | vendored verifier (MIT), provenance in `PROVENANCE.md` |
| `tests/` | unit, scenario, receipt/tamper, and API tests |
| `docs/` | `RULES_NOTES.md`, `EVALUATION.md` (generated), `TRACE_EXAMPLE.md` |

## Honest limits

* Synthetic scenes only so far. Real OAK-D recordings are needed before any real-world accuracy claim (`DESIGN.md` section 9).
* Depth cannot tell *what* an object is; claims say "a region became farther by 24 mm", not "a bottle was removed".
* Changes under ~1 sigma after filtering are invisible (documented miss: `subthreshold_item_removed`).
* The LLM planner is an interface plus tests with a fake model; no model is called.
* AWS pieces (`INFRA/`, `DynamoStore`) are a draft that has never run on AWS.

## License

MIT (see `LICENSE`). Vendored `third_party/prove_it_done` is MIT as well.
