"""Claims and receipts. No OpenCV import here.

Every statement the agent makes that someone may act on ("the shelf item is gone", "the camera lost depth",
"depth drifted +1.8%, compensated") becomes a Claim: a small JSON file that pins WHAT was asserted, WHERE
(pixel region), WHEN (frame windows) and AGAINST WHAT INPUT (clip digest). Receipts re-run independent checks
of that claim against the input, in prove-it-done's ledger format, and the vendored verifier judges them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

CLAIM_VERSION = 1


@dataclass
class Claim:
    id: str
    run: str
    kind: str                       # object_removed | object_added | sensor_fault | sensor_degraded | drift_compensated
    zone: str
    region: list[int]               # r0, r1, c0, c1 in frame pixels
    evidence: list[int]             # frame window [e0, e1) that shows it
    baseline: list[int]             # frame window [b0, b1) of the empty-scene calibration
    asserted: dict                  # {"dz_mm": ...} | {"valid_frac": ...} | {"gain": ...}
    clip: dict                      # {"path": str|None, "scenario": str, "seed": int, "digest": str}
    created_frame: int
    text: str
    tol: dict = field(default_factory=lambda: {"abs_mm": 8.0, "rel": 0.25, "valid_abs": 0.08, "gain_abs": 0.01})
    version: int = CLAIM_VERSION

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, sort_keys=True) + "\n"


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_claims(claims: list[Claim], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir) / "claims"
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for c in claims:
        p = out / f"{c.id}.json"
        p.write_text(c.to_json())
        paths.append(p)
    return paths


def ledger_text(claims: list[Claim], paths: list[Path], title: str) -> str:
    """prove-it-done ledger: each claim is a done step with three receipts through different instruments."""
    lines = [f"# Receipts: {title}", ""]
    for i, (c, p) in enumerate(zip(claims, paths), 1):
        rel = p.as_posix()
        lines += [
            f"- [x] {i}. {c.id}: {c.text}",
            f"  - receipt: `file:{rel} | sha256={sha256_file(p)}` ",
            f"  - receipt: `cmd:python -m guetteur.check_numpy {rel} | contains=PASS | via=numpy-region-stats`",
            f"  - receipt: `cmd:python -m guetteur.check_cv {rel} | contains=PASS | via=opencv-region-stats`",
        ]
    return "\n".join(lines) + "\n"
