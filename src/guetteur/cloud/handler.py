"""API Gateway (HTTP API, payload v2) -> Lambda handler. Runs unchanged on a laptop (`guetteur serve`) and on AWS.

Routes
  GET  /                      judge dashboard (single HTML page, no external assets)
  GET  /api/scenarios         list of synthetic scenarios
  POST /api/run               {scenario, seed, planner, gate} -> trace, claims, alerts, score, receipt results   (OpenCV 5 runs here)
  POST /api/verify            {claim, tamper?} -> PASS/FAIL from BOTH receipt checks, re-run on the pinned synthetic input
  POST /api/evidence          edge -> cloud evidence bundle (JSON only; pictures rejected)
  GET  /api/approvals         pending human-approval requests
  POST /api/approvals/{id}    {approve: bool, by: str}
  GET  /healthz

Public routes only accept scenario names and small JSON; they never accept images or arbitrary uploads, so a visitor
cannot make the function process anything but the built-in synthetic scenes.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from .. import check_cv, check_numpy, synth
from ..common import depth_digest
from ..evalrun import score_run
from ..runner import run_scenario
from .store import DynamoStore, EvidenceRejected, MemoryStore, validate_evidence

HERE = Path(__file__).parent
_STORE = DynamoStore(os.environ["TABLE_NAME"]) if os.environ.get("TABLE_NAME") else MemoryStore()
MAX_BODY = 20_000


def _resp(code: int, body: Any, ctype: str = "application/json") -> dict:
    return {"statusCode": code, "headers": {"content-type": ctype, "cache-control": "no-store", "x-content-type-options": "nosniff"},
            "body": body if isinstance(body, str) else json.dumps(body, default=str)}


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    if len(raw) > MAX_BODY:
        raise ValueError("request body too large")
    out = json.loads(raw)
    if not isinstance(out, dict):
        raise ValueError("JSON object expected")
    return out


def run_checks(claim: dict) -> dict:
    """Both independent receipt checks, in-process. The claim pins its input by digest."""
    depth = check_numpy.load_depth(claim)
    res = {}
    for name, mod in (("numpy-region-stats", check_numpy), ("opencv-region-stats", check_cv)):
        ok, msg = mod.check(claim, depth)
        res[name] = {"pass": bool(ok), "detail": msg}
    res["proven"] = all(v["pass"] for v in res.values())
    return res


def handle(event: dict, context: Any = None) -> dict:
    t0 = time.perf_counter()
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    try:
        if method == "GET" and path in ("/", "/index.html"):
            return _resp(200, (HERE / "dashboard.html").read_text(), "text/html; charset=utf-8")
        if method == "GET" and path == "/healthz":
            import cv2
            return _resp(200, {"ok": True, "opencv": cv2.__version__, "arch": os.uname().machine})
        if method == "GET" and path == "/api/scenarios":
            return _resp(200, [{"name": n, "note": s.note, "frames": s.T} for n, s in synth.scenarios().items()])
        if method == "POST" and path == "/api/run":
            b = _body(event)
            name, seed = b.get("scenario"), int(b.get("seed", 0))
            if name not in synth.scenarios() or not (0 <= seed <= 99):
                return _resp(400, {"error": "unknown scenario or seed (0..99)"})
            planner, gate = b.get("planner", "rule"), b.get("gate", "approve")
            if planner not in ("rule", "naive-guarded", "naive") or gate not in ("approve", "deny", "timeout"):
                return _resp(400, {"error": "planner in {rule, naive-guarded, naive}, gate in {approve, deny, timeout}"})
            res, meta = run_scenario(name, seed, planner, gate)
            checks = {c.id: run_checks(c.__dict__) for c in res.claims[:6]}
            return _resp(200, {
                "synthetic": True, "scenario": name, "seed": seed, "planner": res.planner, "gate": gate, "note": meta["note"],
                "score": score_run(res, meta), "counters": res.counters,
                "trace": [e for e in res.trace if e["action"]["kind"] != "continue"][:80],
                "claims": [c.__dict__ for c in res.claims[:6]], "alerts": res.alerts, "receipts": checks,
                "runtime_ms": {"server_total": round((time.perf_counter() - t0) * 1000, 1),
                               "median_frame_loop": round(float(np.median(res.frame_ms)), 2)}})
        if method == "POST" and path == "/api/verify":
            b = _body(event)
            claim = b.get("claim")
            if not isinstance(claim, dict) or claim.get("clip", {}).get("scenario") not in synth.scenarios():
                return _resp(400, {"error": "claim must reference a built-in synthetic scenario"})
            claim["clip"]["path"] = None                                # never read paths chosen by a visitor
            return _resp(200, run_checks(claim))
        if method == "POST" and path == "/api/evidence":
            doc = validate_evidence(_body(event))
            eid = _STORE.put_evidence(doc)
            aid = _STORE.request_approval({"claim": doc["claim"], "summary": doc.get("summary", "")}) if doc.get("needs_approval") else None
            return _resp(202, {"evidence_id": eid, "approval_id": aid})
        if method == "GET" and path == "/api/approvals":
            return _resp(200, _STORE.list_approvals())
        if method == "POST" and path.startswith("/api/approvals/"):
            b = _body(event)
            return _resp(200, _STORE.decide(path.rsplit("/", 1)[1], bool(b.get("approve")), str(b.get("by", "anonymous"))[:64]))
        return _resp(404, {"error": "not found"})
    except EvidenceRejected as e:
        return _resp(422, {"error": str(e)})
    except KeyError:
        return _resp(404, {"error": "unknown id"})
    except (ValueError, json.JSONDecodeError) as e:
        return _resp(400, {"error": str(e)})
    except SystemExit as e:                                             # a failed digest in check_numpy.load_depth
        return _resp(200, {"proven": False, "error": str(e)})


lambda_handler = handle
