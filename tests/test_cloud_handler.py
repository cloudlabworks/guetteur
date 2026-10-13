import json

from guetteur.cloud import handler as H
from guetteur.cloud.store import MemoryStore


def call(method, path, body=None):
    r = H.handle({"requestContext": {"http": {"method": method}}, "rawPath": path, "body": json.dumps(body) if body is not None else None})
    return r["statusCode"], (json.loads(r["body"]) if r["headers"]["content-type"] == "application/json" else r["body"])


def test_dashboard_and_health():
    code, html = call("GET", "/")
    assert code == 200 and "SYNTHETIC" in html and "<script>" in html
    code, h = call("GET", "/healthz")
    assert code == 200 and h["opencv"].startswith("5.")


def test_run_endpoint_returns_trace_claims_and_receipts():
    code, d = call("POST", "/api/run", {"scenario": "subtle_item_removed", "seed": 0})
    assert code == 200 and d["synthetic"] is True and d["score"]["success"]
    assert [e["action"]["kind"] for e in d["trace"]] == ["recheck_zone", "request_approval"]
    assert d["claims"] and all(v["proven"] for v in d["receipts"].values())


def test_verify_endpoint_accepts_truth_and_rejects_a_lie():
    _, d = call("POST", "/api/run", {"scenario": "shelf_item_removed", "seed": 0})
    claim = d["claims"][0]
    code, ok = call("POST", "/api/verify", {"claim": claim})
    assert code == 200 and ok["proven"]
    claim["asserted"]["dz_mm"] *= 2
    code, bad = call("POST", "/api/verify", {"claim": claim})
    assert code == 200 and not bad["proven"] and not bad["numpy-region-stats"]["pass"] and not bad["opencv-region-stats"]["pass"]


def test_verify_never_reads_visitor_chosen_paths():
    _, d = call("POST", "/api/run", {"scenario": "shelf_item_removed", "seed": 0})
    claim = d["claims"][0]
    claim["clip"]["path"] = "/etc/passwd"
    code, r = call("POST", "/api/verify", {"claim": claim})
    assert code == 200 and r["proven"]            # path ignored; input regenerated from (scenario, seed) and digest-checked


def test_bad_inputs_are_rejected_cleanly():
    assert call("POST", "/api/run", {"scenario": "../../etc/passwd"})[0] == 400
    assert call("POST", "/api/run", {"scenario": "empty", "seed": 10 ** 6})[0] == 400
    assert call("POST", "/api/run", {"scenario": "empty", "planner": "rm -rf"})[0] == 400
    assert call("POST", "/api/verify", {"claim": {"clip": {"scenario": "nope"}}})[0] == 400
    assert call("POST", "/api/verify", {"claim": "x"})[0] == 400
    assert call("GET", "/nope")[0] == 404
    r = H.handle({"requestContext": {"http": {"method": "POST"}}, "rawPath": "/api/run", "body": "x" * 30000})
    assert r["statusCode"] == 400
    r = H.handle({"requestContext": {"http": {"method": "POST"}}, "rawPath": "/api/run", "body": "{not json"})
    assert r["statusCode"] == 400


def test_evidence_endpoint_refuses_pictures():
    assert call("POST", "/api/evidence", {"claim": {"id": "c1"}, "image": "AAAA"})[0] == 422
    assert call("POST", "/api/evidence", {"claim": {"id": "c1"}, "summary": "ok"})[0] == 202


def test_approval_flow_and_default_deny(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(H, "_STORE", store)
    _, r = call("POST", "/api/evidence", {"claim": {"id": "c1"}, "needs_approval": True})
    aid = r["approval_id"]
    assert [a["status"] for a in call("GET", "/api/approvals")[1]] == ["pending"]
    code, a = call("POST", f"/api/approvals/{aid}", {"approve": True, "by": "alex"})
    assert code == 200 and a["status"] == "approved" and a["by"] == "alex"
    assert call("POST", f"/api/approvals/{aid}", {"approve": False})[0] == 400          # already decided
    assert call("POST", "/api/approvals/nope", {"approve": True})[0] == 404
    _, r2 = call("POST", "/api/evidence", {"claim": {"id": "c2"}, "needs_approval": True})
    store.approvals[r2["approval_id"]]["expires"] = 0                                    # nobody answered in time
    statuses = {a["id"]: a["status"] for a in call("GET", "/api/approvals")[1]}
    assert statuses[r2["approval_id"]] == "denied_timeout"
    assert call("POST", f"/api/approvals/{r2['approval_id']}", {"approve": True})[0] == 400
