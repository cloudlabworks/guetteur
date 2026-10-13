"""OpenCV 5 perception tools. Input: depth frames in millimetres. Output: small JSON-safe observations.

Pipeline per zone and frame (all cv2 except the arithmetic between steps):

1. hole handling      cv2.inpaint (TELEA, 16-bit) fills invalid stereo pixels
2. denoise            cv2.medianBlur (5x5, 16-bit)
3. change evidence    signed difference to a temporal-median baseline, divided by a range-dependent sigma model
4. clean-up           cv2.morphologyEx OPEN then CLOSE with an elliptical kernel
5. blobs              cv2.connectedComponentsWithStats; physical size from the pinhole model
6. motion             cv2.calcOpticalFlowFarneback between consecutive frames, averaged per blob
7. coarse evidence    cv2.resize(INTER_AREA) to a 6x8 signed-delta grid (this is all that leaves the device)

The planner never sees pixels: it gets ``ZoneObservation.summary()``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .common import F_PX, SIGMA_COEF, Zone


@dataclass
class PerceptionParams:
    k_mask: float = 2.0              # per-pixel |z| above which a pixel is "changed"
    min_area_mm2: float = 8000.0     # ignore blobs smaller than ~9 x 9 cm
    median_ksize: int = 5
    open_ksize: int = 3
    close_ksize: int = 5
    flow_scale_mm: float = 4000.0    # depth range mapped to 0..255 for optical flow
    occupant_min_height_mm: float = 900.0
    occupant_max_width_mm: float = 1100.0
    occupant_min_dz_mm: float = 300.0
    iou_match: float = 0.3


@dataclass
class Blob:
    bbox: tuple[int, int, int, int]       # r0, r1, c0, c1 in FRAME pixel coordinates
    cx: float                             # normalised to the zone
    cy: float
    area_mm2: float
    z_mm: float                           # median depth of the blob
    dz_mm: float                          # median signed change vs the baseline (+ farther, - nearer)
    snr: float                            # mean |z-score| inside the blob
    width_mm: float
    height_mm: float
    flow_px: float                        # mean optical-flow magnitude inside the blob (px/frame)
    occupant: bool                        # geometry says "a person-sized thing that is nearer than the scene"
    zone_frac: float = 0.0                # share of the zone the blob covers

    def to_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class ZoneObservation:
    zone: str
    frame: int
    valid_frac: float                 # valid pixels now / valid pixels in the baseline
    noise_ratio: float                # measured robust sigma of the residual / modelled sigma (1.0 = nominal)
    shift: float                      # residual global gain error (median relative difference outside blobs)
    blobs: list[Blob] = field(default_factory=list)
    persist: int = 0                  # consecutive frames the strongest non-occupant blob has been in place
    integrated_n: int = 1             # >1 when this observation integrates several frames
    grid: list[list[int]] = field(default_factory=list)   # 6x8 signed delta in mm: the only spatial evidence kept
    runtime_ms: float = 0.0

    @property
    def occupants(self) -> list[Blob]:
        return [b for b in self.blobs if b.occupant]

    @property
    def changes(self) -> list[Blob]:
        return sorted((b for b in self.blobs if not b.occupant), key=lambda b: -b.snr * math.sqrt(b.area_mm2))

    def summary(self) -> dict:
        return dict(zone=self.zone, frame=self.frame, valid_frac=round(self.valid_frac, 3),
                    noise_ratio=round(self.noise_ratio, 2), shift=round(self.shift, 4), persist=self.persist,
                    integrated_n=self.integrated_n, occupants=len(self.occupants),
                    blobs=[b.to_dict() for b in self.changes[:3]], grid=self.grid)


class _ZoneState:
    def __init__(self) -> None:
        self.base: np.ndarray | None = None        # float32 baseline of the zone crop (mm)
        self.sigma_a = SIGMA_COEF                  # fitted noise coefficient (raw frames)
        self.filt_gain = 1.0                       # residual sigma after inpaint+median filter, relative to sigma_a
        self.n_cal = 1                             # frames behind the baseline (its own noise is a floor)
        self.valid_base = 1.0
        self.gain = 1.0                            # drift compensation (multiplies the baseline)
        self.prev8: np.ndarray | None = None
        self.prev_boxes: list[tuple[int, int, int, int]] = []
        self.run = 0


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    r0, r1 = max(a[0], b[0]), min(a[1], b[1])
    c0, c1 = max(a[2], b[2]), min(a[3], b[3])
    inter = max(0, r1 - r0) * max(0, c1 - c0)
    ua = (a[1] - a[0]) * (a[3] - a[2]) + (b[1] - b[0]) * (b[3] - b[2]) - inter
    return inter / ua if ua else 0.0


class Perception:
    def __init__(self, zones: tuple[Zone, ...], params: PerceptionParams | None = None) -> None:
        self.zones = {z.name: z for z in zones}
        self.p = params or PerceptionParams()
        self.state = {z.name: _ZoneState() for z in zones}

    # ------------------------------------------------------------------ calibration
    def calibrate(self, frames: np.ndarray) -> None:
        """Baseline = per-pixel temporal median of an empty-scene window; noise model fitted from the same window."""
        for name, zone in self.zones.items():
            r0, r1, c0, c1 = zone.px()
            st = self.state[name]
            crop = frames[:, r0:r1, c0:c1].astype(np.float32)
            valid = crop > 0
            masked = np.where(valid, crop, np.nan)
            base = np.nanmedian(masked, axis=0)
            vf = valid.mean(axis=0)
            hole = np.isnan(base)
            if hole.any():                                           # never-valid pixels: fill from the zone median
                base[hole] = np.nanmedian(base)
            mad = np.nanmedian(np.abs(masked - base[None]), axis=0) * 1.4826
            ok = (vf > 0.8) & np.isfinite(mad)
            self.state[name].sigma_a = float(np.clip(np.median(mad[ok] / base[ok] ** 2), 2e-6, 4e-5))
            st.base = base.astype(np.float32)
            st.valid_base = float(valid.mean())
            # the filter removes most of the noise: measure how much, on the calibration frames themselves
            model = float(np.median(st.sigma_a * st.base ** 2))
            resid = [self._prefilter(f.astype(np.uint16))[0].astype(np.float32) - st.base for f in frames[:8, r0:r1, c0:c1]]
            st.filt_gain = float(np.clip(1.4826 * np.median(np.abs(np.stack(resid))) / model, 0.05, 1.0))
            st.n_cal = len(frames)
            st.gain, st.prev8, st.prev_boxes, st.run = 1.0, None, [], 0

    def accept_baseline(self, zone: str, frames: np.ndarray) -> None:
        """Adopt the current scene as the new baseline (after a human decided on a reported change)."""
        r0, r1, c0, c1 = self.zones[zone].px()
        crop = frames[:, r0:r1, c0:c1].astype(np.float32)
        base = np.nanmedian(np.where(crop > 0, crop, np.nan), axis=0)
        st = self.state[zone]
        hole = np.isnan(base)
        base[hole] = st.base[hole] * st.gain
        st.base, st.gain, st.run, st.prev_boxes = base.astype(np.float32), 1.0, 0, []
        st.n_cal = len(frames)                                   # a shorter window means a noisier baseline

    def compensate_drift(self, zone: str, rel: float) -> float:
        st = self.state[zone]
        st.gain *= 1.0 + rel
        return st.gain

    def reset_zone(self, zone: str) -> None:
        st = self.state[zone]
        st.run, st.prev_boxes, st.prev8 = 0, [], None

    # ------------------------------------------------------------------ observation
    def observe(self, frame: np.ndarray, t: int, zone: str, k: float | None = None) -> ZoneObservation:
        return self._analyze(frame[self._slice(zone)], t, zone, 1, k, flow=True)

    def observe_window(self, frames: np.ndarray, t: int, zone: str, k: float | None = None) -> ZoneObservation:
        """Integrate a short window (temporal median) before analysing: the 'recheck' tool."""
        r0, r1, c0, c1 = self.zones[zone].px()
        crop = frames[:, r0:r1, c0:c1].astype(np.float32)
        med = np.nanmedian(np.where(crop > 0, crop, np.nan), axis=0)
        hole = np.isnan(med)
        st = self.state[zone]
        med[hole] = 0.0
        return self._analyze(med.astype(np.uint16), t, zone, len(frames), k, flow=False)

    @staticmethod
    def _sigma_scale(st: "_ZoneState", n: int) -> float:
        """Residual sigma as a fraction of the raw sigma model after filtering and integrating n frames.
        The baseline's own noise (1.2533/sqrt(n_cal)) is a floor that integration cannot remove."""
        floor = 1.2533 / math.sqrt(st.n_cal)
        g1 = math.sqrt(max(st.filt_gain ** 2 - floor ** 2, 0.01))
        return math.sqrt(g1 * g1 / n + floor * floor)

    def _prefilter(self, crop16: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """cv2.inpaint fills invalid stereo pixels (skipped when most of the frame is gone: it would invent data),
        cv2.medianBlur removes speckle."""
        valid = crop16 > 0
        if 0.3 < valid.mean() < 1.0:
            filled = cv2.inpaint(crop16, (~valid).astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
        else:
            filled = crop16.copy()
        return cv2.medianBlur(filled, self.p.median_ksize), valid

    def _slice(self, zone: str) -> tuple[slice, slice]:
        r0, r1, c0, c1 = self.zones[zone].px()
        return slice(r0, r1), slice(c0, c1)

    def _analyze(self, crop16: np.ndarray, t: int, zone: str, n: int, k: float | None, flow: bool) -> ZoneObservation:
        t_start = cv2.getTickCount()
        p, st = self.p, self.state[zone]
        assert st.base is not None, "calibrate() first"
        k = p.k_mask if k is None else k
        r0, _, c0, _ = self.zones[zone].px()
        valid = crop16 > 0
        valid_frac = float(valid.mean()) / max(st.valid_base, 1e-6)
        base = st.base * st.gain

        # 1-2 hole handling + denoise
        filt, _ = self._prefilter(crop16)

        # 3 change evidence in units of sigma (the sigma model shrinks with integration)
        sigma = np.maximum(self._sigma_scale(st, n) * st.sigma_a * base * base, 2.0)
        diff = filt.astype(np.float32) - base
        zs = diff / sigma
        usable = valid if n == 1 else (crop16 > 0)

        # 4 clean-up
        mask = ((np.abs(zs) > k) & usable).astype(np.uint8)
        ko = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.open_ksize, p.open_ksize))
        kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.close_ksize, p.close_ksize))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ko)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kc)

        # global health: residual noise and gain error from the pixels that did NOT change structurally
        # (|z| < 8 drops objects and people but keeps a uniform gain error, which is what we want to measure)
        core = usable & (np.abs(zs) < 8.0)
        if core.sum() > 200:
            zo = zs[core]
            noise_ratio = float(1.4826 * np.median(np.abs(zo - np.median(zo))))     # centred: a gain error is not noise
            shift = float(np.median(diff[core] / base[core]))
        else:
            noise_ratio, shift = 0.0, 0.0

        # 6 motion (Farneback on an 8-bit rendering of consecutive frames)
        cur8 = cv2.convertScaleAbs(filt, alpha=255.0 / p.flow_scale_mm)
        mag = None
        if flow and st.prev8 is not None and valid.mean() > 0.3:
            fl = cv2.calcOpticalFlowFarneback(st.prev8, cur8, None, 0.5, 2, 11, 2, 5, 1.1, 0)
            mag = cv2.magnitude(fl[..., 0], fl[..., 1])
        st.prev8 = cur8 if flow else st.prev8

        # 5 blobs
        blobs: list[Blob] = []
        nlab, labels, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
        h, w = mask.shape
        for i in range(1, nlab):
            x, y, bw, bh, area = (int(v) for v in stats[i])
            sel = labels == i
            zmed = float(np.median(filt[sel]))
            if zmed <= 0:
                continue
            mm_px = zmed / F_PX
            area_mm2 = area * mm_px * mm_px
            if area_mm2 < p.min_area_mm2:
                continue
            dz = float(np.median(diff[sel]))
            snr = float(np.mean(np.abs(zs[sel])))
            width_mm, height_mm = bw * mm_px, bh * mm_px
            tall = height_mm >= p.occupant_min_height_mm or (y == 0 and y + bh >= h)
            occupant = bool(dz <= -p.occupant_min_dz_mm and tall and width_mm <= p.occupant_max_width_mm)
            blobs.append(Blob((r0 + y, r0 + y + bh, c0 + x, c0 + x + bw), float(cent[i][0] / w), float(cent[i][1] / h),
                              area_mm2, zmed, dz, snr, width_mm, height_mm,
                              float(mag[sel].mean()) if mag is not None else 0.0, occupant, area / (h * w)))

        # persistence of the strongest non-occupant blob (IoU tracking across frames; integration inherits the run)
        obs = ZoneObservation(zone, t, valid_frac, noise_ratio, shift, blobs, integrated_n=n)
        top = obs.changes[:1]
        if top and not obs.occupants:
            box = top[0].bbox
            if any(_iou(box, b) >= p.iou_match for b in st.prev_boxes):
                st.run += n
            else:
                st.run = n
            st.prev_boxes = [b.bbox for b in obs.changes]
        else:
            st.run, st.prev_boxes = 0, []
        obs.persist = st.run

        # 7 coarse evidence grid (mm, signed)
        gridf = cv2.resize(np.where(usable, diff, 0).astype(np.float32), (8, 6), interpolation=cv2.INTER_AREA)
        obs.grid = [[int(round(v)) for v in row] for row in gridf]
        obs.runtime_ms = (cv2.getTickCount() - t_start) * 1000.0 / cv2.getTickFrequency()
        return obs
