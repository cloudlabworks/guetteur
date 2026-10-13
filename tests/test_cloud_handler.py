import json

from guetteur.cloud import handler as H
from guetteur.cloud.store import MemoryStore


KEY = "k" * 32


def call(method, path, body=None, headers=None):
    r = H.handle({"requestContext": {"http": {"method": method}}, "rawPath": path, "body": json.dumps(body) if body is not None else None,
                  "headers": headers or {}})
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


def test_write_routes_fail_shut_without_a_key_and_with_a_wrong_key(monkeypatch):
    monkeypatch.delenv("EDGE_KEY", raising=False)
    for m, p in (("POST", "/api/evidence"), ("GET", "/api/approvals"), ("POST", "/api/approvals/abc")):
        assert call(m, p, {"claim": {}}, {"x-edge-key": KEY})[0] == 403          # no key configured: closed even if one is sent
    monkeypatch.setenv("EDGE_KEY", KEY)
    for m, p in (("POST", "/api/evidence"), ("GET", "/api/approvals"), ("POST", "/api/approvals/abc")):
        assert call(m, p, {"claim": {}})[0] == 403
        assert call(m, p, {"claim": {}}, {"x-edge-key": "wrong" * 8})[0] == 403
    monkeypatch.setenv("EDGE_KEY", "short")
    assert call("GET", "/api/approvals", None, {"x-edge-key": "short"})[0] == 403       # weak keys are refused too
    assert call("GET", "/api/scenarios")[0] == 200                                      # public read-only routes are unaffected


def test_evidence_endpoint_refuses_pictures(monkeypatch):
    monkeypatch.setenv("EDGE_KEY", KEY)
    hdr = {"X-Edge-Key": KEY}
    assert call("POST", "/api/evidence", {"claim": {"id": "c1"}, "image": "AAAA"}, hdr)[0] == 422
    assert call("POST", "/api/evidence", {"claim": {"id": "c1"}, "summary": "ok"}, hdr)[0] == 202


def test_every_scenario_runs_through_the_endpoint_fast_and_proven():
    """The judge endpoint must finish well inside the 25 s function timeout for every scenario (slow_drift has the most claims)."""
    import time
    worst = 0.0
    for name in __import__("guetteur.synth", fromlist=["x"]).scenarios():
        t = time.perf_counter()
        code, d = call("POST", "/api/run", {"scenario": name, "seed": 0})
        worst = max(worst, time.perf_counter() - t)
        assert code == 200 and all(v["proven"] for v in d["receipts"].values()), name
    assert worst < 12.0, f"slowest scenario took {worst:.1f}s here; Lambda will be slower"


def test_approval_flow_and_default_deny(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(H, "_STORE", store)
    monkeypatch.setenv("EDGE_KEY", KEY)

    def keyed(method, path, body=None):                    # every call in this test carries the edge key
        return call(method, path, body, {"x-edge-key": KEY})
    _, r = keyed("POST", "/api/evidence", {"claim": {"id": "c1"}, "needs_approval": True})
    aid = r["approval_id"]
    assert [a["status"] for a in keyed("GET", "/api/approvals")[1]] == ["pending"]
    code, a = keyed("POST", f"/api/approvals/{aid}", {"approve": True, "by": "alex"})
    assert code == 200 and a["status"] == "approved" and a["by"] == "alex"
    assert keyed("POST", f"/api/approvals/{aid}", {"approve": False})[0] == 400          # already decided
    assert keyed("POST", "/api/approvals/nope", {"approve": True})[0] == 404
    _, r2 = keyed("POST", "/api/evidence", {"claim": {"id": "c2"}, "needs_approval": True})
    store.approvals[r2["approval_id"]]["expires"] = 0                                    # nobody answered in time
    statuses = {a["id"]: a["status"] for a in keyed("GET", "/api/approvals")[1]}
    assert statuses[r2["approval_id"]] == "denied_timeout"
    assert keyed("POST", f"/api/approvals/{r2['approval_id']}", {"approve": True})[0] == 400
