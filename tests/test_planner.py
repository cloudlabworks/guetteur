import json

import pytest

from guetteur.perception import Blob, ZoneObservation
from guetteur.planner import (Action, LLMPlanner, NaivePlanner, PlanView, PlannerConfig, PolicyViolation, RulePlanner,
                           ZoneRT, validate_action)

CFG = PlannerConfig()


def blob(snr=10.0, dz=300.0, occupant=False, zone_frac=0.1, w=300, h=300):
    return Blob((50, 90, 40, 80), .5, .5, 70_000.0, 1800.0, dz, snr, w, h, 0.0, occupant, zone_frac)


def view(blobs=(), valid=1.0, noise=1.0, shift=0.0, persist=0, n=1, rt=None, t=100):
    o = ZoneObservation("shelf", t, valid, noise, shift, list(blobs), persist, n)
    return PlanView(t, o, rt or ZoneRT(), CFG)


P = RulePlanner()


def test_clear_change_waits_for_persistence_then_asks_a_human():
    assert P.plan(view([blob()], persist=3)).kind == "continue"
    a = P.plan(view([blob()], persist=10))
    assert (a.kind, a.args["kind"]) == ("request_approval", "object_removed")
    assert P.plan(view([blob(dz=-300)], persist=10)).args["kind"] == "object_added"
    assert a.cited["snr"] == 10.0                                  # the decision cites its evidence


def test_ambiguous_evidence_triggers_a_recheck_sized_by_the_evidence():
    weak = P.plan(view([blob(snr=2.1)], persist=8))
    mid = P.plan(view([blob(snr=3.8)], persist=8))
    assert weak.kind == mid.kind == "recheck_zone"
    assert weak.args["n"] > mid.args["n"] >= 5                      # weaker evidence -> more frames requested
    assert P.plan(view([blob(snr=3.8)], persist=3)).args["n"] == 7   # ... and enough frames for the change to count as held
    assert P.plan(view([blob(snr=3.8)], persist=1)).kind == "continue"      # not persistent enough to spend a recheck yet


def test_integrated_confirmation_goes_to_a_human_and_unconfirmed_is_dropped():
    assert P.plan(view([blob(snr=5.5)], persist=10, n=7)).kind == "request_approval"
    rt = ZoneRT(rechecks_used=3)
    assert P.plan(view([blob(snr=3.0)], persist=10, n=7, rt=rt)).kind == "continue"      # budget exhausted: not claimed


def test_below_noise_floor_is_ignored():
    assert P.plan(view([blob(snr=1.2)], persist=50)).kind == "continue"


def test_people_pause_the_zone():
    a = P.plan(view([blob(snr=20, dz=-900, occupant=True, w=500, h=1700)], persist=0))
    assert a.kind == "pause_zone" and a.args["frames"] == CFG.pause_frames


def test_sensor_loss_waits_then_escalates_and_partial_loss_degrades_first():
    assert P.plan(view(valid=0.08, rt=ZoneRT(fault_streak=1))).kind == "continue"
    a = P.plan(view(valid=0.08, rt=ZoneRT(fault_streak=3)))
    assert (a.kind, a.args["kind"]) == ("request_approval", "sensor_fault")
    assert P.plan(view(valid=0.6)).kind == "degrade_zone"
    assert P.plan(view(valid=0.6, rt=ZoneRT(degraded_streak=40))).args["kind"] == "sensor_degraded"


def test_noise_burst_degrades_and_suppresses_claims():
    assert P.plan(view([blob(snr=6)], noise=3.0, persist=30)).kind == "degrade_zone"
    assert P.plan(view([blob(snr=6)], noise=3.0, persist=30, rt=ZoneRT(degraded_until=200), t=100)).kind == "continue"


def test_drift_is_compensated_not_reported_until_it_is_too_big():
    a = P.plan(view(shift=0.02))
    assert a.kind == "compensate_drift" and a.args["gain"] == 0.02
    assert P.plan(view(shift=0.05, rt=ZoneRT(total_gain=0.18))).kind == "request_approval"


def test_blob_covering_most_of_the_zone_is_treated_as_global_effect():
    assert P.plan(view([blob(snr=3.0, zone_frac=0.7)], persist=5)).kind == "continue"


def test_naive_baseline_alerts_on_anything_big_and_ignores_health():
    n = NaivePlanner()
    acts = [n.plan(view([blob(snr=20, dz=-900, occupant=True)], persist=0, t=t)).kind for t in range(12)]
    assert "request_approval" in acts                                # a walking person becomes an "alert"
    assert NaivePlanner().plan(view(valid=0.05)).kind == "continue"  # camera blind: says nothing


def test_validate_action_enforces_schema_and_bounds():
    ok = validate_action({"kind": "recheck_zone", "args": {"n": 8, "k": 2.0}, "rationale": "x"}, "shelf")
    assert isinstance(ok, Action)
    for bad in ({"kind": "delete_all"}, {"kind": "recheck_zone", "args": {"n": 9999, "k": 2}},
                {"kind": "recheck_zone", "args": {"n": 8, "k": 99}}, {"kind": "pause_zone", "args": {"frames": 0}},
                {"kind": "compensate_drift", "args": {"gain": 0.9}}, {"kind": "request_approval", "args": {"kind": "send_email"}},
                {"kind": "request_approval"}, "not a dict"):
        with pytest.raises(PolicyViolation):
            validate_action(bad, "shelf")


def test_llm_slot_accepts_valid_json_and_falls_back_on_garbage_or_out_of_policy():
    v = view([blob()], persist=10)
    good = LLMPlanner(lambda prompt: json.dumps({"kind": "continue", "rationale": "llm says wait"}))
    assert good.plan(v).rationale == "llm says wait" and good.rejections == 0
    for out in ("I think we should alert!", json.dumps({"kind": "format_disk"}),
                json.dumps({"kind": "recheck_zone", "args": {"n": 10 ** 6, "k": 2}})):
        llm = LLMPlanner(lambda prompt, o=out: o)
        a = llm.plan(v)
        assert a.kind == "request_approval" and a.rationale.startswith("[llm output rejected")      # rule planner's choice
        assert llm.rejections == 1


def test_llm_prompt_contains_evidence_json_and_no_pixels():
    seen = []
    LLMPlanner(lambda p: seen.append(p) or json.dumps({"kind": "continue"})).plan(view([blob()], persist=4))
    assert '"snr"' in seen[0] and "valid_frac" in seen[0] and len(seen[0]) < 6000
