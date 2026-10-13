"""The perception -> decision -> action loop, its human gate, policy and trace.

Per frame and zone: OpenCV 5 observes; if the observation is salient the planner is asked for one action; the
executor checks it against the autonomy policy and runs it. Actions change the NEXT perception call:
``recheck_zone`` collects n more frames and re-runs OpenCV on their temporal median with new parameters;
``pause_zone`` stops analysing a zone; ``compensate_drift`` rescales the baseline; ``request_approval`` stops
at a human gate (default on no answer: deny) and, if approved, publishes an alert and adopts the new baseline.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from .common import CALIB_FRAMES, FPS, ZONES, Zone
from .perception import Perception, PerceptionParams, ZoneObservation
from .planner import (AUTONOMOUS, GATED, LIMITS, Action, PlanView, Planner, PlannerConfig, PolicyViolation, RulePlanner,
                      ZoneRT, validate_action)
from .receipts import Claim


# ----------------------------------------------------------------------------- human gate

@dataclass
class ApprovalRequest:
    id: str
    kind: str
    zone: str
    frame: int
    claim_id: str
    summary: str
    evidence: dict


@dataclass
class Decision:
    approved: bool
    by: str
    reason: str


class HumanGate(Protocol):
    def decide(self, req: ApprovalRequest) -> Decision | None:
        """Return a Decision, or None when nobody answered in time. None is treated as DENY."""


class ScriptedGate:
    """Test/eval gate: 'approve', 'deny' or 'timeout' for every request, or a callable(req) -> Decision | None."""

    def __init__(self, policy: str | Callable[[ApprovalRequest], Decision | None] = "approve") -> None:
        self.policy = policy
        self.log: list[ApprovalRequest] = []

    def decide(self, req: ApprovalRequest) -> Decision | None:
        self.log.append(req)
        if callable(self.policy):
            return self.policy(req)
        if self.policy == "approve":
            return Decision(True, "scripted-operator", "approved by script")
        if self.policy == "deny":
            return Decision(False, "scripted-operator", "denied by script")
        return None


# ----------------------------------------------------------------------------- results

@dataclass
class RunResult:
    run: str
    planner: str
    trace: list[dict] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)     # every approval request with its outcome
    alerts: list[dict] = field(default_factory=list)       # only approved ones: what actually left the system
    counters: dict = field(default_factory=dict)
    perception_ms: list[float] = field(default_factory=list)   # per zone-frame
    planner_ms: list[float] = field(default_factory=list)      # per planner call
    frame_ms: list[float] = field(default_factory=list)        # per frame, all zones + planner + executor


@dataclass
class _RT(ZoneRT):
    paused_until: int = -1
    collecting: dict | None = None
    cooldown: dict = field(default_factory=dict)
    sensor_alerted: bool = False        # an approved sensor alert stays quiet until depth coverage recovers
    suppressed_logged: set = field(default_factory=set)


def _salient(o: ZoneObservation, rt: _RT, t: int) -> bool:
    """Attention gating: the planner (possibly an LLM) is only called when the evidence is not boring."""
    return bool(o.blobs or o.valid_frac < 0.95 or o.noise_ratio > 1.4 or abs(o.shift) > 0.004 or t < rt.degraded_until)


class Agent:
    def __init__(self, planner: Planner | None = None, gate: HumanGate | None = None, zones: tuple[Zone, ...] = ZONES,
                 cfg: PlannerConfig | None = None, params: PerceptionParams | None = None, guardrails: bool = True) -> None:
        self.planner = planner or RulePlanner()
        self.guardrails = guardrails         # False only for the 'no agent, no guardrails' ablation baseline
        self.gate = gate or ScriptedGate("timeout")          # no human attached = nothing is ever approved
        self.zones, self.cfg, self.params = zones, cfg or PlannerConfig(), params

    def run(self, depth: np.ndarray, run_id: str, clip_ref: dict) -> RunResult:
        P = Perception(self.zones, self.params)
        P.calibrate(depth[:CALIB_FRAMES])
        res = RunResult(run=run_id, planner=getattr(self.planner, "name", "planner"))
        c = dict(frames=0, planner_calls=0, skipped_not_salient=0, skipped_paused=0, rechecks=0, pauses=0, degrades=0,
                 compensations=0, requests=0, approved=0, denied=0, timeouts=0, policy_rejections=0)
        rts = {z.name: _RT() for z in self.zones}
        recent: deque[np.ndarray] = deque(maxlen=12)
        claim_n = 0

        def log(t: int, zone: str, obs: ZoneObservation | None, action: Action, outcome: dict, ms: float) -> None:
            res.trace.append({"step": len(res.trace), "frame": t, "zone": zone, "planner": res.planner,
                              "observation": obs.summary() if obs else None, "action": action.to_dict(),
                              "outcome": outcome, "planner_ms": round(ms, 3)})

        def make_claim(kind: str, zone: str, region: list[int], window: list[int], asserted: dict, text: str, t: int) -> Claim:
            nonlocal claim_n
            claim_n += 1
            return Claim(f"c{claim_n}", run_id, kind, zone, region, window, [0, CALIB_FRAMES], asserted, clip_ref, t, text)

        for t in range(CALIB_FRAMES, len(depth)):
            f0 = time.perf_counter()
            frame = depth[t]
            recent.append(frame)
            c["frames"] += 1
            for z in self.zones:
                rt, name = rts[z.name], z.name
                if t < rt.paused_until:
                    c["skipped_paused"] += 1
                    continue
                if rt.collecting is not None:                          # a recheck is gathering frames
                    rt.collecting["buf"].append(frame)
                    if len(rt.collecting["buf"]) < rt.collecting["n"]:
                        continue
                    p0 = time.perf_counter()
                    obs = P.observe_window(np.stack(rt.collecting["buf"]), t, name, rt.collecting["k"])
                    res.perception_ms.append((time.perf_counter() - p0) * 1000)
                    rt.collecting = None
                else:
                    p0 = time.perf_counter()
                    obs = P.observe(frame, t, name)
                    res.perception_ms.append((time.perf_counter() - p0) * 1000)
                # streaks the planner reads
                rt.fault_streak = rt.fault_streak + 1 if obs.valid_frac < self.cfg.fault_valid else 0
                rt.degraded_streak = rt.degraded_streak + 1 if obs.valid_frac < self.cfg.degrade_valid else 0
                if not obs.changes:
                    rt.rechecks_used = 0                                # episode over
                if obs.valid_frac >= 0.9:
                    rt.sensor_alerted = False
                if not _salient(obs, rt, t):
                    c["skipped_not_salient"] += 1
                    continue

                q0 = time.perf_counter()
                action = self.planner.plan(PlanView(t, obs, rt, self.cfg))
                ms = (time.perf_counter() - q0) * 1000
                res.planner_ms.append(ms)
                c["planner_calls"] += 1
                action.zone = action.zone or name
                try:                                                    # policy: the executor does not trust the planner
                    action = validate_action(action.to_dict(), name) if action.kind != "continue" else action
                    if action.kind not in AUTONOMOUS | GATED:
                        raise PolicyViolation(f"action {action.kind!r} not allowed")
                    if action.kind == "recheck_zone" and rt.rechecks_used >= LIMITS["max_rechecks_per_episode"]:
                        raise PolicyViolation("recheck budget for this episode is used up")
                    if action.kind == "request_approval":                    # a claim needs current evidence behind it
                        k = action.args["kind"]
                        if k.startswith("object") and not obs.blobs:
                            raise PolicyViolation("object claim without any blob in the current observation")
                        if self.guardrails:
                            top = obs.changes[0] if obs.changes else None
                            if k.startswith("object") and not (
                                    top and top.snr >= self.cfg.conf_snr and obs.persist >= self.cfg.hold_frames
                                    and obs.noise_ratio <= self.cfg.burst_ratio and not obs.occupants and obs.valid_frac >= self.cfg.degrade_valid):
                                raise PolicyViolation("object claim needs a confident, persistent blob in a healthy, unoccupied zone")
                            if k.startswith("sensor") and obs.valid_frac >= 0.9 and abs(obs.shift) < 0.05:
                                raise PolicyViolation("sensor claim but depth coverage and gain look healthy")
                    if action.kind == "compensate_drift" and abs(rt.total_gain + action.args["gain"]) > LIMITS["max_total_gain"]:
                        raise PolicyViolation("total drift compensation would exceed the autonomous limit")
                except PolicyViolation as e:
                    c["policy_rejections"] += 1
                    log(t, name, obs, action, {"status": "rejected_by_policy", "detail": str(e)}, ms)
                    continue

                outcome: dict = {"status": "ok"}
                if action.kind == "recheck_zone":
                    rt.collecting = {"n": action.args["n"], "k": action.args["k"], "buf": []}
                    rt.rechecks_used += 1
                    c["rechecks"] += 1
                    outcome["detail"] = f"collecting {action.args['n']} frames"
                elif action.kind == "pause_zone":
                    rt.paused_until = t + action.args["frames"]
                    rt.pauses += 1
                    P.reset_zone(name)
                    c["pauses"] += 1
                    outcome["detail"] = f"zone not analysed until frame {rt.paused_until}"
                elif action.kind == "degrade_zone":
                    rt.degraded_until = t + action.args["frames"]
                    c["degrades"] += 1
                    outcome["detail"] = f"change claims suppressed until frame {rt.degraded_until}"
                elif action.kind == "compensate_drift":
                    g = action.args["gain"]
                    P.compensate_drift(name, g)
                    rt.total_gain += g
                    c["compensations"] += 1
                    r0, r1, c0, c1 = z.px()
                    cl = make_claim("drift_compensated", name, [r0, r1, c0, c1], [t - 7, t + 1], {"gain": round(rt.total_gain, 4)},
                                    f"{name} depth is {rt.total_gain:+.1%} off the calibration baseline; baseline rescaled, no scene change reported", t)
                    res.claims.append(cl)
                    outcome["claim"] = cl.id
                elif action.kind == "request_approval":
                    rt_key = "sensor" if action.args["kind"].startswith("sensor") else action.args["kind"]
                    cd = max(r.cooldown.get(rt_key, -1) for r in rts.values()) if rt_key == "sensor" else rt.cooldown.get(rt_key, -1)
                    if t < cd or (rt_key == "sensor" and any(r.sensor_alerted for r in rts.values())):
                        c["suppressed"] = c.get("suppressed", 0) + 1
                        if rt_key in rt.suppressed_logged:
                            continue                                    # already told the trace once; stay quiet
                        rt.suppressed_logged.add(rt_key)
                        outcome = {"status": "suppressed", "detail": "same alert was already decided (cooldown / sensor fault already reported)"}
                    else:
                        outcome = self._escalate(res, c, P, rts, rt, z, obs, action, t, recent, make_claim, rt_key)
                log(t, name, obs, action, outcome, ms)
                rt.last_actions = (rt.last_actions + [action.kind])[-6:]
            res.frame_ms.append((time.perf_counter() - f0) * 1000)
        res.counters = c
        return res

    # ------------------------------------------------------------------ the gated action
    def _escalate(self, res: RunResult, c: dict, P: Perception, rts: dict, rt: _RT, z: Zone, obs: ZoneObservation,
                  action: Action, t: int, recent: deque, make_claim: Callable, key: str) -> dict:
        kind = action.args["kind"]
        e0 = max(CALIB_FRAMES, t - max(7, obs.integrated_n - 1))
        if kind.startswith("sensor"):                               # only frames that actually show the fault
            streak = rt.fault_streak if kind == "sensor_fault" else rt.degraded_streak
            e0 = max(CALIB_FRAMES, t - min(7, max(streak, 1)) + 1)
        if kind.startswith("object"):
            b = obs.changes[0] if obs.changes else max(obs.blobs, key=lambda x: x.snr)
            r0, r1, c0, c1 = b.bbox
            m_r, m_c = int((r1 - r0) * 0.15), int((c1 - c0) * 0.15)           # shrink: skip the blob's rim
            region = [r0 + m_r, r1 - m_r, c0 + m_c, c1 - m_c]
            asserted = {"dz_mm": round(b.dz_mm, 1)}
            text = (f"{z.name}: a {int(b.area_mm2 / 100)} cm2 region became {'farther' if b.dz_mm > 0 else 'nearer'} by "
                    f"{abs(b.dz_mm):.0f} mm and stayed so for {obs.persist} frames ({kind.replace('_', ' ')})")
        else:
            r0, r1, c0, c1 = z.px()
            region, asserted = [r0, r1, c0, c1], {"valid_frac": round(obs.valid_frac * P.state[z.name].valid_base, 2)}
            text = f"{z.name}: only {asserted['valid_frac']:.0%} of the zone returns depth ({kind.replace('_', ' ')}); scene judgements suspended"
        claim = make_claim(kind, z.name, region, [e0, t + 1], asserted, text, t)
        res.claims.append(claim)
        req = ApprovalRequest(f"a{len(res.requests) + 1}", kind, z.name, t, claim.id, text, {**action.cited, "claim": claim.id})
        c["requests"] += 1
        dec = self.gate.decide(req)
        rec = {"request": req.__dict__, "decision": None, "frame": t}
        if dec is None:
            c["timeouts"] += 1
            status, detail = "denied_timeout", "no human answered; default is deny, nothing was published"
        elif dec.approved:
            c["approved"] += 1
            status, detail = "approved", f"approved by {dec.by}: alert published"
            alert = {"frame": t, "kind": kind, "zone": z.name, "claim": claim.id, "text": text, "approved_by": dec.by}
            res.alerts.append(alert)
            if kind.startswith("object"):                                  # the human has seen it: it becomes the new normal
                P.accept_baseline(z.name, np.stack(list(recent)[-6:]))
                rt.rechecks_used = 0
        else:
            c["denied"] += 1
            status, detail = "denied", f"denied by {dec.by}: nothing published"
        rec["decision"] = {"status": status, "by": getattr(dec, "by", None), "reason": getattr(dec, "reason", None)}
        res.requests.append(rec)
        cooldown = t + (100 if status != "approved" else 40)
        for r in rts.values():                                                  # sensor alerts are camera-wide
            if key == "sensor" or r is rt:
                r.cooldown[key] = cooldown
                r.suppressed_logged.discard(key)
                r.sensor_alerted = r.sensor_alerted or (key == "sensor" and status == "approved")
        return {"status": status, "detail": detail, "claim": claim.id, "request": req.id}
