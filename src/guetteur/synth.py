"""Synthetic depth scenes with ground truth. Everything produced here is SYNTHETIC and labelled as such.

A scene is a stack of uint16 millimetre depth frames (0 = invalid) shaped like OAK-D stereo output:
range-dependent noise (sigma ~ 0.8e-5 * z^2), speckle holes, a dead stripe at the left edge.
No colour, no images: the whole project works on depth only.

Each scenario carries machine-readable ground truth (``truth``) and the outcome the agent is expected to
produce (``expect``) so the evaluation can score it without a human in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .common import CALIB_FRAMES, F_PX, FPS, H, SIGMA_COEF, W, save_clip

SHELF_ROWS, SHELF_COLS = (48, 216), (16, 160)
BOX_A = dict(rows=(90, 130), cols=(40, 76), relief=-300.0)       # big, obvious item
BOX_B = dict(rows=(140, 176), cols=(100, 136), relief=-25.0)     # subtle item: ambiguous in one frame, clear when integrated
BOX_C = dict(rows=(60, 90), cols=(110, 140), relief=-10.0)       # below noise: a documented miss


@dataclass
class Rect:
    rows: tuple[int, int]
    cols: tuple[int, int]
    relief: float                 # millimetres added to the surface underneath (negative = nearer)
    t0: int = 0
    t1: int | None = None         # None = until the end


@dataclass
class Person:
    z: float
    height: float                 # mm
    width: float                  # mm
    path: Callable[[int], float]  # frame -> centre column in pixels
    t0: int
    t1: int


@dataclass
class Scenario:
    name: str
    T: int
    note: str
    rects: list[Rect] = field(default_factory=list)
    persons: list[Person] = field(default_factory=list)
    gain: tuple[int, float] | None = None                       # (t_start, fractional gain per frame)
    bursts: list[tuple[int, int, float]] = field(default_factory=list)   # (t0, t1, noise multiplier)
    holes: list[tuple[int, tuple[int, int], tuple[int, int], float]] = field(default_factory=list)  # (t0, rows, cols, p_invalid)
    truth: list[dict] = field(default_factory=list)
    expect: dict = field(default_factory=dict)


def _surface() -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    z = 3200.0 + 300.0 * xx / W                      # back wall, slightly tilted
    r, c = SHELF_ROWS, SHELF_COLS
    z[r[0]:r[1], c[0]:c[1]] = 1800.0 + 60.0 * xx[r[0]:r[1], c[0]:c[1]] / W   # shelf face
    return z


def _person_mask(p: Person, cx: float) -> tuple[np.ndarray, float]:
    s = p.z / F_PX                                   # mm per pixel at the person's range
    h_px, a = p.height / s, p.width / s / 2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    bottom = H - 5.0
    top = bottom - h_px
    body = ((xx - cx) / a) ** 2 + ((yy - (top + 0.62 * h_px)) / (0.38 * h_px)) ** 2 <= 1.0
    head = (xx - cx) ** 2 + (yy - (top + 0.07 * h_px)) ** 2 <= (0.075 * h_px) ** 2
    return body | head, s


def render(sc: Scenario, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = _surface()
    out = np.zeros((sc.T, H, W), dtype=np.uint16)
    for t in range(sc.T):
        z = base.copy()
        for r in sc.rects:
            if t >= r.t0 and (r.t1 is None or t < r.t1):
                z[r.rows[0]:r.rows[1], r.cols[0]:r.cols[1]] += r.relief
        for p in sc.persons:
            if p.t0 <= t < p.t1:
                cx = p.path(t)
                m, _ = _person_mask(p, cx)
                z[m] = p.z + 30.0 * np.sin(np.arange(W, dtype=np.float32)[None, :].repeat(H, 0)[m] / 9.0)
        if sc.gain and t >= sc.gain[0]:
            z = z * (1.0 + sc.gain[1] * (t - sc.gain[0]))
        mult = 1.0
        for b0, b1, m in sc.bursts:
            if b0 <= t < b1:
                mult = m
        z = z + rng.normal(0.0, 1.0, z.shape).astype(np.float32) * (SIGMA_COEF * z * z) * mult
        d = np.clip(z, 200, 8000)
        invalid = rng.random(z.shape) < 0.02                      # speckle holes
        invalid[:, :12] = True                                    # stereo left-edge dead stripe
        for h0, rows, cols, p_inv in sc.holes:
            if t >= h0:
                invalid[rows[0]:rows[1], cols[0]:cols[1]] |= rng.random((rows[1] - rows[0], cols[1] - cols[0])) < p_inv
        d[invalid] = 0
        out[t] = d.astype(np.uint16)
    return out


def _walk(x0: float, dx: float, t0: int) -> Callable[[int], float]:
    return lambda t: x0 + dx * (t - t0)


def _lingering(t_in: int, t_out: int, x_edge: float, x_stay: float, speed: float) -> Callable[[int], float]:
    def f(t: int) -> float:
        if t < t_in + (x_stay - x_edge) / speed:
            return x_edge + speed * (t - t_in)
        if t < t_out:
            return x_stay + 3.0 * np.sin((t - t_in) * 0.9)        # sway while standing at the shelf
        return x_stay - speed * (t - t_out)
    return f


def scenarios() -> dict[str, Scenario]:
    A, B, C = BOX_A, BOX_B, BOX_C
    o = CALIB_FRAMES - 25            # all event times below were written for a 25-frame calibration window
    sc: dict[str, Scenario] = {}

    def add(s: Scenario) -> None:
        sc[s.name] = s

    def esc(kind: str, zone: str, t0: int, by: int, **kw) -> dict:
        return dict(kind=kind, zone=zone, t0=t0 + o, by=by + o, **{k: v + o for k, v in kw.items()})

    add(Scenario("empty", 150 + o, "Control: nothing happens. Any alert is a false alarm.",
                 rects=[Rect(**A), Rect(**B)], truth=[], expect=dict(escalations=[], forbid=["escalate"])))
    add(Scenario("shelf_item_removed", 150 + o, "The obvious item (300 mm proud of the shelf) is taken at frame 60.",
                 rects=[Rect(**A, t1=60 + o), Rect(**B)],
                 truth=[esc("object_change", "shelf", 60, 90)],
                 expect=dict(escalations=[esc("object_change", "shelf", 60, 90)])))
    add(Scenario("shelf_item_added", 150 + o, "A new item appears on the shelf at frame 60.",
                 rects=[Rect(**A), Rect(**B), Rect(rows=(70, 110), cols=(90, 126), relief=-300.0, t0=60 + o)],
                 truth=[esc("object_change", "shelf", 60, 90)],
                 expect=dict(escalations=[esc("object_change", "shelf", 60, 90)])))
    add(Scenario("subtle_item_removed", 170 + o, "A low item (25 mm relief, ambiguous in a single frame) is taken at frame 60: the agent has to ask for more evidence before it claims anything.",
                 rects=[Rect(**A), Rect(**B, t1=60 + o)],
                 truth=[esc("object_change", "shelf", 60, 100)],
                 expect=dict(escalations=[esc("object_change", "shelf", 60, 100)], min_rechecks=1)))
    add(Scenario("person_walkthrough", 150 + o, "A person crosses the doorway zone. Presence is not a change: no alert, zone paused while occupied.",
                 rects=[Rect(**A), Rect(**B)],
                 persons=[Person(2400, 1700, 500, _walk(170, 6.0, 50 + o), 50 + o, 85 + o)],
                 truth=[], expect=dict(escalations=[], forbid=["escalate"], min_pauses=1)))
    add(Scenario("person_takes_item", 200 + o, "A person walks up to the shelf, takes the obvious item (frame 70), leaves by 110. The alert may only fire after they leave.",
                 rects=[Rect(**A, t1=70 + o), Rect(**B)],
                 persons=[Person(1200, 1700, 500, _lingering(50 + o, 100 + o, -20.0, 90.0, 7.0), 50 + o, 118 + o)],
                 truth=[esc("object_change", "shelf", 70, 150, not_before=100)],
                 expect=dict(escalations=[esc("object_change", "shelf", 70, 150, not_before=100)], min_pauses=1)))
    add(Scenario("noise_burst", 150 + o, "Six frames of 6x stereo noise (e.g. a flickering IR source). Transient, so no claim should be made.",
                 rects=[Rect(**A), Rect(**B)], bursts=[(70 + o, 76 + o, 6.0)],
                 truth=[], expect=dict(escalations=[], forbid=["escalate"])))
    add(Scenario("slow_drift", 200 + o, "Depth gain drifts 0.04% per frame from frame 30 (thermal/calibration drift, +6.8% by the end). Not an object: compensate, do not alert.",
                 rects=[Rect(**A), Rect(**B)], gain=(30 + o, 4e-4),
                 truth=[], expect=dict(escalations=[], forbid=["escalate"], min_compensations=1)))
    add(Scenario("lens_obstructed", 150 + o, "From frame 70 the camera loses 92% of its depth (covered lens / washed-out IR). Must raise a sensor fault, not claim scene changes.",
                 rects=[Rect(**A), Rect(**B)], holes=[(70 + o, (0, H), (0, W), 0.92)],
                 truth=[esc("sensor_fault", "*", 70, 90)],
                 expect=dict(escalations=[esc("sensor_fault", "*", 70, 90)])))
    add(Scenario("partial_washout", 190 + o, "From frame 70 about half the shelf zone loses depth (sunlit window). Degrade first, escalate if it persists.",
                 rects=[Rect(**A), Rect(**B)], holes=[(70 + o, (48, 216), (90, 160), 0.9)],
                 truth=[esc("sensor_fault", "shelf", 70, 150)],
                 expect=dict(escalations=[esc("sensor_fault", "shelf", 70, 150)])))
    add(Scenario("subthreshold_item_removed", 150 + o, "FAILURE CASE: a 10 mm-relief item (about 1 sigma after filtering) is removed at frame 60. Below the noise floor; the system is expected to miss it.",
                 rects=[Rect(**A), Rect(**C, t1=60 + o)],
                 truth=[esc("object_change", "shelf", 60, 100)],
                 expect=dict(escalations=[esc("object_change", "shelf", 60, 100)], known_miss=True)))
    return sc


def make_clip(name: str, seed: int, out_dir: str) -> str:
    s = scenarios()[name]
    depth = render(s, seed)
    path = f"{out_dir}/{name}_s{seed}.npz"
    meta = dict(synthetic=True, label="SYNTHETIC depth scene (not a real camera recording)", scenario=name, seed=seed,
                fps=FPS, calib_frames=CALIB_FRAMES, note=s.note, truth=s.truth, expect=s.expect)
    save_clip(path, depth, meta)
    return path
