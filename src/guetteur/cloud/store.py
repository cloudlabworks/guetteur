"""Approvals + evidence store. MemoryStore is what runs locally and in tests. DynamoStore is the AWS wiring
(DRAFT: written for the SAM template in INFRA/, not yet run against AWS)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

FORBIDDEN_KEYS = {"image", "jpeg", "png", "frame", "rgb", "color", "colour", "pixels", "depth_map", "video"}
MAX_BYTES = 16_384


class EvidenceRejected(ValueError):
    pass


def validate_evidence(doc: Any) -> dict:
    """Edge -> cloud evidence bundle: small JSON only (claims, observation summaries, 6x8 delta grids). No pictures, ever."""
    raw = json.dumps(doc)
    if len(raw) > MAX_BYTES:
        raise EvidenceRejected(f"evidence bundle larger than {MAX_BYTES} bytes")
    bad: list[str] = []

    def walk(x: Any, path: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in FORBIDDEN_KEYS:
                    bad.append(f"{path}/{k}")
                walk(v, f"{path}/{k}")
        elif isinstance(x, list):
            if len(x) > 64:
                bad.append(f"{path}: list longer than 64 entries")
            for i, v in enumerate(x[:64]):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, str) and len(x) > 600:
            bad.append(f"{path}: string longer than 600 chars")
    walk(doc)
    if bad:
        raise EvidenceRejected("rejected: " + "; ".join(bad[:5]))
    if not isinstance(doc, dict) or "claim" not in doc:
        raise EvidenceRejected("evidence bundle needs a 'claim'")
    return doc


class MemoryStore:
    def __init__(self) -> None:
        self.approvals: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}

    def put_evidence(self, doc: dict) -> str:
        eid = uuid.uuid4().hex[:12]
        self.evidence[eid] = dict(doc, received=int(time.time()))
        return eid

    def request_approval(self, req: dict, ttl_s: int = 300) -> str:
        aid = uuid.uuid4().hex[:12]
        self.approvals[aid] = dict(req, id=aid, status="pending", expires=int(time.time()) + ttl_s)
        return aid

    def list_approvals(self) -> list[dict]:
        now = time.time()
        for a in self.approvals.values():
            if a["status"] == "pending" and a["expires"] < now:
                a["status"] = "denied_timeout"        # default on no answer: deny
        return sorted(self.approvals.values(), key=lambda a: a["expires"])

    def decide(self, aid: str, approve: bool, by: str) -> dict:
        self.list_approvals()
        a = self.approvals.get(aid)
        if a is None:
            raise KeyError(aid)
        if a["status"] != "pending":
            raise ValueError(f"approval is already {a['status']}")
        a["status"] = "approved" if approve else "denied"
        a["by"] = by
        return a


class DynamoStore(MemoryStore):
    """DRAFT. Table has partition key `pk` (string) and TTL attribute `expires`. Not exercised in tests."""

    def __init__(self, table_name: str) -> None:
        import boto3                                   # provided by the Lambda runtime
        self.table = boto3.resource("dynamodb").Table(table_name)

    def put_evidence(self, doc: dict) -> str:
        eid = uuid.uuid4().hex[:12]
        self.table.put_item(Item={"pk": f"ev#{eid}", "doc": json.dumps(doc), "expires": int(time.time()) + 7 * 86400})
        return eid

    def request_approval(self, req: dict, ttl_s: int = 300) -> str:
        aid = uuid.uuid4().hex[:12]
        self.table.put_item(Item={"pk": f"ap#{aid}", "doc": json.dumps(dict(req, id=aid)), "status": "pending",
                                  "expires": int(time.time()) + ttl_s})
        return aid

    def list_approvals(self) -> list[dict]:
        from boto3.dynamodb.conditions import Attr
        items = self.table.scan(FilterExpression=Attr("pk").begins_with("ap#"))["Items"]
        out = []
        for it in items:
            d = json.loads(it["doc"])
            d["status"] = it["status"] if not (it["status"] == "pending" and int(it["expires"]) < time.time()) else "denied_timeout"
            out.append(d)
        return out

    def decide(self, aid: str, approve: bool, by: str) -> dict:
        r = self.table.update_item(Key={"pk": f"ap#{aid}"}, UpdateExpression="SET #s = :n, decided_by = :b",
                                   ConditionExpression="#s = :p AND expires > :now", ExpressionAttributeNames={"#s": "status"},
                                   ExpressionAttributeValues={":n": "approved" if approve else "denied", ":p": "pending", ":b": by,
                                                              ":now": int(time.time())}, ReturnValues="ALL_NEW")
        return r["Attributes"]
