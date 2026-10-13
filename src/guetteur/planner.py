"""Planners: decide the next action from OpenCV 5 evidence. The planner never sees pixels.

``Planner.plan(view) -> Action`` is the whole interface. ``RulePlanner`` is deterministic and is what the
tests and the evaluation run. ``LLMPlanner`` shows where a language model slots in: it receives the same JSON
view, must answer with the same action schema, and anything outside the executor's policy is rejected and
replaced by the rule planner's choice. ``NaivePlanner`` is the ablation baseline ("threshold and alert").
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .perception import ZoneObservation

# Autonomy tiers (enforced by the executor, not by the planner):
AUTONOMOUS = {"continue", "recheck_zone", "pause_zone", "degrade_zone", "compensate_drift"}
GATED = {"request_approval"}          # needs a human decision before anything leaves the system
APPROVAL_KINDS = {"object_removed", "object_added", "sensor_fault", "sensor_degraded"}

LIMITS = {"recheck_n": (3, 40), "recheck_k": (1.5, 4.0), "pause_frames": (1, 30), "degrade_frames": (1, 30),
          "gain": (-0.05, 0.05), "max_total_gain": 0.2, "max_rechecks_per_episode": 3}


@dataclass
class Action:
    kind: str
    zone: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    cited: dict[str, Any] = field(default_factory=dict)    # the evidence numbers the decision rests on

    def to_dict(self) -> dict:
        return {"kind": self.kind, "zone": self.zone, "args": self.args, "rationale": self.rationale, "cited": self.cited}


class PolicyViolation(ValueError):
    pass


@dataclass
class PlannerConfig:
    conf_snr: float = 4.5            # blob contrast (mean |z|) above which one frame is convincing
    tent_snr: float = 2.0            # ... below which it is ignored; in between it is "ambiguous"
    hold_frames: int = 10            # a change must persist this long before anyone is asked (2 s at 5 fps)
    min_persist_recheck: int = 3
    fault_valid: float = 0.5
    fault_frames: int = 3
    degrade_valid: float = 0.8
    degrade_escalate_frames: int = 30
    burst_ratio: float = 1.6         # residual noise / modelled noise above this = noise burst
    drift_trigger: float = 0.012
    pause_frames: int = 8
    k_recheck: float = 2.0


@dataclass
class ZoneRT:
    """Per-zone runtime state the executor keeps; read-only for planners."""
    rechecks_used: int = 0
    fault_streak: int = 0
    degraded_streak: int = 0
    degraded_until: int = -1
    total_gain: float = 0.0
    pauses: int = 0
    last_actions: list[str] = field(default_factory=list)


@dataclass
class PlanView:
    t: int
    obs: ZoneObservation
    rt: ZoneRT
    cfg: PlannerConfig

    def to_prompt_dict(self) -> dict:
        return {"t": self.t, "observation": self.obs.summary(),
                "state": {"rechecks_used": self.rt.rechecks_used, "fault_streak": self.rt.fault_streak,
                          "degraded_streak": self.rt.degraded_streak, "total_gain": round(self.rt.total_gain, 4),
                          "last_actions": self.rt.last_actions[-3:]},
                "thresholds": {k: v for k, v in self.cfg.__dict__.items()}}


class Planner(Protocol):
    name: str

    def plan(self, view: PlanView) -> Action: ...


def _kind_for(dz_mm: float) -> str:
    return "object_removed" if dz_mm > 0 else "object_added"


class RulePlanner:
    name = "rule-v1"

    def plan(self, v: PlanView) -> Action:
        o, c, rt, z = v.obs, v.cfg, v.rt, v.obs.zone

        def act(name: str, why: str, cited: dict | None = None, **args: Any) -> Action:
            return Action(name, z, args, why, cited or {})

        # 1. sensor health comes first: nothing visual can be trusted if the sensor is not
        if o.valid_frac < c.fault_valid:
            cited = {"valid_frac": round(o.valid_frac, 2), "fault_streak": rt.fault_streak}
            if rt.fault_streak >= c.fault_frames:
                return act("request_approval", "depth coverage collapsed and stayed down: ask a human to look at the camera",
                           cited, kind="sensor_fault")
            return act("continue", f"valid depth fell to {o.valid_frac:.0%}; waiting to see if it persists "
                                   f"({rt.fault_streak}/{c.fault_frames})", cited)
        if o.valid_frac < c.degrade_valid:
            cited = {"valid_frac": round(o.valid_frac, 2), "degraded_streak": rt.degraded_streak}
            if rt.degraded_streak >= c.degrade_escalate_frames:
                return act("request_approval", "partial depth loss has lasted too long to be transient", cited, kind="sensor_degraded")
            if v.t >= rt.degraded_until:
                return act("degrade_zone", f"only {o.valid_frac:.0%} of the zone has depth: stop making change claims here", cited, frames=15)
            return act("continue", "zone degraded; change claims suppressed", cited)
        if o.noise_ratio > c.burst_ratio:
            cited = {"noise_ratio": round(o.noise_ratio, 2)}
            if v.t >= rt.degraded_until:
                return act("degrade_zone", f"residual noise is {o.noise_ratio:.1f}x the model: treat blobs as noise, wait it out", cited, frames=8)
            return act("continue", "noise burst in progress", cited)

        # 2. slow gain drift is a sensor property, not a scene event
        if abs(o.shift) >= c.drift_trigger:
            cited = {"shift": round(o.shift, 4), "total_gain": round(rt.total_gain, 4)}
            if abs(rt.total_gain + o.shift) > LIMITS["max_total_gain"]:
                return act("request_approval", "depth gain has drifted beyond what we compensate automatically", cited, kind="sensor_fault")
            return act("compensate_drift", f"whole-zone depth is off by {o.shift:+.1%}: rescale the baseline instead of reporting it",
                       cited, gain=round(o.shift, 4))

        # 3. people are not scenery
        if o.occupants:
            b = o.occupants[0]
            return act("pause_zone", "a person-sized, nearer-than-scene blob is present: do not judge scene changes while they are here",
                       {"width_mm": int(b.width_mm), "height_mm": int(b.height_mm), "dz_mm": int(b.dz_mm)}, frames=c.pause_frames)

        if v.t < rt.degraded_until:
            return act("continue", "zone degraded; change claims suppressed")
        ch = o.changes
        if not ch or ch[0].snr < c.tent_snr:
            return act("continue", "nothing above the noise floor")
        b = ch[0]
        cited = {"snr": round(b.snr, 1), "dz_mm": int(b.dz_mm), "area_cm2": int(b.area_mm2 / 100), "persist": o.persist,
                 "integrated_n": o.integrated_n}

        if b.snr >= c.conf_snr:
            if o.persist >= c.hold_frames:
                return act("request_approval", f"clear change ({b.dz_mm:+.0f} mm) held for {o.persist} frames", cited, kind=_kind_for(b.dz_mm))
            return act("continue", f"clear change, waiting for it to persist ({o.persist}/{c.hold_frames})", cited)

        # ambiguous evidence: ask for more evidence instead of guessing
        if b.zone_frac > 0.4:
            return act("continue", f"ambiguous blob spans {b.zone_frac:.0%} of the zone: a global effect (gain drift), not an object", cited)
        if rt.rechecks_used < LIMITS["max_rechecks_per_episode"]:
            if o.integrated_n == 1 and o.persist < c.min_persist_recheck:
                return act("continue", "ambiguous blob, not yet persistent enough to spend a recheck", cited)
            need = math.ceil(1.6 * (1.1 * c.conf_snr / max(b.snr, 0.5)) ** 2)
            n = int(min(max(need, c.hold_frames - o.persist, 5), LIMITS["recheck_n"][1]))
            return act("recheck_zone", f"contrast {b.snr:.1f} is between {c.tent_snr} and {c.conf_snr}: integrate {n} frames and look again",
                       cited, n=n, k=c.k_recheck)
        return act("continue", "ambiguous after the recheck budget was used: dropped, not claimed", cited)


class NaivePlanner:
    """Ablation baseline: alert on any blob above the confident threshold that persists. No rechecks, no health,
    no drift handling, no notion of people."""
    name = "naive-threshold"

    def __init__(self, cfg: PlannerConfig | None = None) -> None:
        self.cfg = cfg or PlannerConfig()
        self.run: dict[str, int] = {}

    def plan(self, v: PlanView) -> Action:
        o, c = v.obs, self.cfg
        top = max(o.blobs, key=lambda b: b.snr, default=None)
        if top is not None and top.snr >= c.conf_snr:
            self.run[o.zone] = self.run.get(o.zone, 0) + 1
        else:
            self.run[o.zone] = 0
        if top is not None and self.run[o.zone] >= c.hold_frames:
            self.run[o.zone] = 0                 # asks again after another hold period if nothing came of it
            return Action("request_approval", o.zone, {"kind": _kind_for(top.dz_mm)}, "blob above threshold for hold_frames",
                          {"snr": round(top.snr, 1), "dz_mm": int(top.dz_mm), "persist": c.hold_frames})
        return Action("continue", o.zone, {}, "below threshold")


def validate_action(d: dict, zone: str) -> Action:
    """Turn untrusted planner output (e.g. an LLM's JSON) into an Action or raise PolicyViolation."""
    if not isinstance(d, dict):
        raise PolicyViolation("action must be a JSON object")
    kind = d.get("kind")
    if kind not in AUTONOMOUS | GATED:
        raise PolicyViolation(f"unknown action {kind!r}; allowed: {sorted(AUTONOMOUS | GATED)}")
    args = d.get("args") or {}
    if not isinstance(args, dict):
        raise PolicyViolation("args must be an object")
    lo, hi = LIMITS["recheck_n"]
    if kind == "recheck_zone":
        if not (isinstance(args.get("n"), int) and lo <= args["n"] <= hi):
            raise PolicyViolation(f"recheck_zone.n must be an int in [{lo}, {hi}]")
        if not (isinstance(args.get("k"), (int, float)) and LIMITS["recheck_k"][0] <= args["k"] <= LIMITS["recheck_k"][1]):
            raise PolicyViolation(f"recheck_zone.k must be in {LIMITS['recheck_k']}")
    if kind in ("pause_zone", "degrade_zone"):
        lo, hi = LIMITS["pause_frames" if kind == "pause_zone" else "degrade_frames"]
        if not (isinstance(args.get("frames"), int) and lo <= args["frames"] <= hi):
            raise PolicyViolation(f"{kind}.frames must be an int in [{lo}, {hi}]")
    if kind == "compensate_drift":
        g = args.get("gain")
        if not (isinstance(g, (int, float)) and LIMITS["gain"][0] <= g <= LIMITS["gain"][1]):
            raise PolicyViolation(f"compensate_drift.gain must be in {LIMITS['gain']}")
    if kind == "request_approval" and args.get("kind") not in APPROVAL_KINDS:
        raise PolicyViolation(f"request_approval.kind must be one of {sorted(APPROVAL_KINDS)}")
    return Action(kind, zone, args, str(d.get("rationale", ""))[:300], dict(d.get("cited") or {}))


class LLMPlanner:
    """Slot for a language model. ``complete(prompt) -> str`` is any text-in/text-out callable (Amazon Bedrock
    Converse, a local model, a test double). Output must be one JSON action; anything else falls back to
    ``fallback`` and the rejection is recorded on the action, so the trace shows it."""
    name = "llm-slot"

    SYSTEM = ("You control a depth-camera monitoring loop. You receive OpenCV 5 evidence as JSON (no images). "
              "Reply with ONE JSON object: {\"kind\": one of %s, \"args\": {...}, \"rationale\": str, \"cited\": {...}}. "
              "Only request_approval reaches a human; never invent evidence.")

    def __init__(self, complete: Callable[[str], str], fallback: Planner | None = None) -> None:
        self.complete = complete
        self.fallback = fallback or RulePlanner()
        self.rejections = 0

    def plan(self, v: PlanView) -> Action:
        prompt = (self.SYSTEM % sorted(AUTONOMOUS | GATED)) + "\n\n" + json.dumps(v.to_prompt_dict())
        try:
            return validate_action(json.loads(self.complete(prompt)), v.obs.zone)
        except (PolicyViolation, json.JSONDecodeError, TypeError) as e:
            self.rejections += 1
            a = self.fallback.plan(v)
            a.rationale = f"[llm output rejected: {e}] " + a.rationale
            return a
