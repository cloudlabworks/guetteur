"""Shared constants, zones and clip I/O. No OpenCV here, so the numpy-only receipt check can import it."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

H, W = 240, 320            # depth frame size (OAK-D stereo is 640x400; we work at half-ish resolution)
FPS = 5                    # analysis rate of the loop (frames per second)
HFOV_DEG = 72.0            # nominal OAK-D mono horizontal field of view
F_PX = (W / 2) / math.tan(math.radians(HFOV_DEG / 2))   # focal length in pixels at this resolution
SIGMA_COEF = 0.8e-5        # stereo depth noise model: sigma_mm = SIGMA_COEF * z_mm**2
CALIB_FRAMES = 60          # first frames of every clip are an empty-scene calibration window (12 s at 5 fps)


@dataclass(frozen=True)
class Zone:
    name: str
    bbox: tuple[float, float, float, float]   # x0, y0, x1, y1 normalised, origin top-left

    def px(self) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = self.bbox
        return int(round(y0 * H)), int(round(y1 * H)), int(round(x0 * W)), int(round(x1 * W))


ZONES: tuple[Zone, ...] = (
    Zone("shelf", (0.05, 0.20, 0.50, 0.90)),
    Zone("doorway", (0.60, 0.00, 1.00, 1.00)),
)
ZONE_BY_NAME = {z.name: z for z in ZONES}


def depth_digest(depth: np.ndarray) -> str:
    """Content hash of a depth stack (shape + little-endian uint16 bytes), independent of the file container."""
    a = np.ascontiguousarray(depth, dtype="<u2")
    h = hashlib.sha256()
    h.update(json.dumps(list(a.shape)).encode())
    h.update(a.tobytes())
    return h.hexdigest()


@dataclass
class Clip:
    depth: np.ndarray          # (T, H, W) uint16 millimetres, 0 = invalid
    meta: dict

    @property
    def digest(self) -> str:
        return depth_digest(self.depth)


def save_clip(path: str | Path, depth: np.ndarray, meta: dict) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(meta, digest=depth_digest(depth))
    blob = np.frombuffer(json.dumps(meta, sort_keys=True).encode(), dtype=np.uint8)
    np.savez(path, depth=depth.astype("<u2"), meta=blob)
    return meta["digest"]


def load_clip(path: str | Path) -> Clip:
    with np.load(path, allow_pickle=False) as z:
        depth = z["depth"]
        meta = json.loads(bytes(z["meta"]).decode())
    return Clip(depth, meta)
