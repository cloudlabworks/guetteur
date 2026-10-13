"""Static guardrail check for INFRA/template.yaml. Dev-time only (needs pyyaml); never runs on AWS.

    uvx --with pyyaml python -I deploy/check_guardrails.py INFRA/template.yaml

Fails if the template loses: a reserved-concurrency cap, 7-day log retention, API throttling,
or if any S3 bucket is not fully private. CloudFormation tags (!Ref, !If, ...) are loaded as plain data.
"""
import sys

import yaml


class _L(yaml.SafeLoader):
    pass


_L.add_multi_constructor("!", lambda loader, suffix, node: {
    "Fn::" + suffix if suffix != "Ref" else "Ref":
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
        else loader.construct_sequence(node, deep=True) if isinstance(node, yaml.SequenceNode)
        else loader.construct_mapping(node, deep=True)})

t = yaml.load(open(sys.argv[1]), Loader=_L)
res, params, errors = t["Resources"], t["Parameters"], []

# 1. reserved concurrency on every function, with a documented default > 0
funcs = {k: v for k, v in res.items() if v["Type"] == "AWS::Serverless::Function"}
for name, f in funcs.items():
    if "ReservedConcurrentExecutions" not in f["Properties"]:
        errors.append(f"{name}: no ReservedConcurrentExecutions")
if int(params["ApiReservedConcurrency"]["Default"]) < 1:
    errors.append("ApiReservedConcurrency default must be >= 1")

# 2. log retention 7 days by default, and no log group without retention
if int(params["LogRetentionDays"]["Default"]) != 7:
    errors.append("LogRetentionDays default must be 7")
groups = {k: v for k, v in res.items() if v["Type"] == "AWS::Logs::LogGroup"}
for name, g in groups.items():
    if g["Properties"].get("RetentionInDays") != {"Ref": "LogRetentionDays"}:
        errors.append(f"{name}: RetentionInDays is not tied to LogRetentionDays")
for name, f in funcs.items():
    if "LoggingConfig" not in f["Properties"]:
        errors.append(f"{name}: no explicit log group (the auto-created one never expires)")

# 3. API throttling
for name, a in {k: v for k, v in res.items() if v["Type"] == "AWS::Serverless::HttpApi"}.items():
    s = a["Properties"].get("DefaultRouteSettings", {})
    if not (0 < s.get("ThrottlingRateLimit", 0) <= 10 and 0 < s.get("ThrottlingBurstLimit", 0) <= 20):
        errors.append(f"{name}: throttling missing or too loose: {s}")

# 4. no public S3
for name, b in {k: v for k, v in res.items() if v["Type"] == "AWS::S3::Bucket"}.items():
    pab = b["Properties"].get("PublicAccessBlockConfiguration", {})
    if not all(pab.get(k) is True for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")):
        errors.append(f"{name}: bucket without full public-access block")
if any(r["Type"] == "AWS::S3::BucketPolicy" for r in res.values()):
    errors.append("a BucketPolicy exists; review for public access")

if errors:
    sys.exit("GUARDRAIL FAIL:\n  " + "\n  ".join(errors))
print(f"guardrails ok: {len(funcs)} function(s) capped, {len(groups)} log group(s) at 7d, throttled HTTP API, "
      f"{sum(r['Type'] == 'AWS::S3::Bucket' for r in res.values())} S3 buckets")
