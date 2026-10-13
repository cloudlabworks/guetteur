import pytest

from conftest import ALL


@pytest.mark.parametrize("name", [n for n in ALL if n != "subthreshold_item_removed"])
@pytest.mark.parametrize("seed", [0, 1])
def test_rule_agent_meets_ground_truth(scored, name, seed):
    res, meta, s = scored(name, seed)
    assert s["success"], (name, seed, s, res.counters, [(a["frame"], a["kind"], a["zone"]) for a in res.alerts])


def test_documented_failure_case_is_a_miss_not_a_false_alarm(scored):
    res, meta, s = scored("subthreshold_item_removed")
    assert s["known_miss"] and s["fn"] == 1 and s["fp"] == 0 and not res.alerts


def test_naive_threshold_baseline_fails_where_the_agent_succeeds(scored):
    wins = []
    for n in ("subtle_item_removed", "person_walkthrough", "slow_drift", "lens_obstructed", "partial_washout", "person_takes_item"):
        _, _, naive = scored(n, 0, "naive")
        _, _, rule = scored(n, 0, "rule")
        assert rule["success"]
        wins.append(not naive["success"])
    assert all(wins), wins


def test_guardrails_alone_stop_false_alarms_but_not_misses(scored):
    """Executor guardrails (evidence preconditions) remove the naive baseline's false alerts; only the agent removes its misses."""
    for n in ("person_walkthrough", "noise_burst"):
        _, _, s = scored(n, 0, "naive-guarded")
        assert s["fp"] == 0, n
    _, _, s = scored("slow_drift", 0, "naive-guarded")
    assert s["fp"] > 0                                     # guardrails do not know about gain drift; the agent's compensation does
    _, _, s = scored("subtle_item_removed", 0, "naive-guarded")
    assert s["fn"] == 1                                    # still blind to ambiguous evidence
    _, _, s = scored("lens_obstructed", 0, "naive-guarded")
    assert s["fn"] == 1                                    # and to sensor faults


def test_evidence_changes_the_next_action_same_code_different_clips(scored):
    """OpenCV output decides the action sequence: ambiguous -> recheck -> approval; burst -> degrade; obvious -> straight to approval."""
    def seq(n):
        res, _, _ = scored(n)
        return [e["action"]["kind"] for e in res.trace if e["action"]["kind"] != "continue"]
    assert seq("subtle_item_removed") == ["recheck_zone", "request_approval"]
    assert seq("shelf_item_removed") == ["request_approval"]
    assert "request_approval" not in seq("noise_burst") and "degrade_zone" in seq("noise_burst")
    assert "compensate_drift" in seq("slow_drift")
    res, _, _ = scored("lens_obstructed")
    ra = [e["outcome"]["status"] for e in res.trace if e["action"]["kind"] == "request_approval"]
    assert ra[0] == "approved" and set(ra[1:]) <= {"suppressed"} and len(res.alerts) == 1     # one camera-wide alert, not one per zone


def test_recheck_arguments_come_from_the_observation(scored):
    res, _, _ = scored("subtle_item_removed")
    rc = next(e for e in res.trace if e["action"]["kind"] == "recheck_zone")
    assert rc["action"]["cited"]["snr"] < 4.5 and rc["action"]["args"]["n"] >= 5
    nxt = next(e for e in res.trace if e["step"] > rc["step"])
    assert nxt["observation"]["integrated_n"] == rc["action"]["args"]["n"]       # the next perception call used them


def test_person_present_means_no_claim_until_they_leave(scored):
    res, meta, s = scored("person_takes_item")
    first = min(a["frame"] for a in res.alerts)
    assert first >= meta["expect"]["escalations"][0]["not_before"]


def test_human_gate_timeout_and_deny_publish_nothing(scored):
    for gate, key in (("timeout", "timeouts"), ("deny", "denied")):
        res, _, _ = scored("shelf_item_removed", 0, "rule", gate)
        assert res.alerts == [] and res.counters["requests"] >= 1 and res.counters[key] >= 1
        assert all(r["decision"]["status"].startswith("denied") for r in res.requests)
        assert res.counters["requests"] == 1                          # no nagging every frame


def test_approval_adopts_new_baseline_so_the_alert_is_not_repeated(scored):
    res, _, _ = scored("shelf_item_removed")
    assert res.counters["requests"] == 1 and len(res.alerts) == 1


def test_executor_policy_blocks_unsupported_claims_and_bad_args():
    import numpy as np
    from guetteur import synth
    from guetteur.agent import Agent, ScriptedGate
    from guetteur.common import depth_digest
    from guetteur.planner import Action

    class Liar:
        name = "liar"
        def plan(self, v):                      # claims an object change on an empty scene
            return Action("request_approval", v.obs.zone, {"kind": "object_removed"}, "trust me")

    d = synth.render(synth.scenarios()["noise_burst"], 0)
    res = Agent(Liar(), ScriptedGate("approve")).run(d, "liar", dict(path=None, scenario="noise_burst", seed=0, digest=depth_digest(d)))
    assert res.alerts == [] and res.counters["requests"] == 0


def test_policy_rejects_out_of_bounds_planner_actions():
    from guetteur import synth
    from guetteur.agent import Agent, ScriptedGate
    from guetteur.common import depth_digest
    from guetteur.planner import Action

    class Reckless:
        name = "reckless"
        def plan(self, v):
            return Action("recheck_zone", v.obs.zone, {"n": 100000, "k": 2.0}, "look forever")

    d = synth.render(synth.scenarios()["shelf_item_removed"], 0)
    res = Agent(Reckless(), ScriptedGate("approve")).run(d, "r", dict(path=None, scenario="x", seed=0, digest=depth_digest(d)))
    assert res.counters["rechecks"] == 0 and res.counters["policy_rejections"] > 0


def test_attention_gating_keeps_planner_calls_low_on_quiet_scenes(scored):
    res, _, _ = scored("empty")
    assert res.counters["planner_calls"] <= 0.02 * res.counters["frames"] * 2


def test_trace_records_are_complete(scored):
    res, _, _ = scored("subtle_item_removed")
    for e in res.trace:
        assert {"step", "frame", "zone", "planner", "observation", "action", "outcome", "planner_ms"} <= e.keys()
        assert e["action"]["rationale"]


def test_loop_fits_the_frame_budget(scored):
    import numpy as np
    res, _, _ = scored("shelf_item_removed")
    assert float(np.median(res.frame_ms)) < 1000 / 5      # 5 fps budget; real figure is ~9 ms on Apple Silicon
