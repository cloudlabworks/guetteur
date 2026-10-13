# INFRA: AWS SAM template (DRAFT, not deployed)

Nothing in this folder has been deployed, and no AWS account or credential was used to write it. It exists so the
architecture in `DESIGN.md` is concrete and reviewable.

| Resource | Why | Cost shape |
| --- | --- | --- |
| `guetteur-api` Lambda, **arm64 / Graviton**, python3.12, 1024 MB | serves the dashboard and runs the OpenCV 5 loop and both receipt checks | pay per request; reserved concurrency 5 caps it |
| `guetteur-evidence` Lambda | edge intake: JSON only, pictures rejected, opens approval requests | pay per request |
| Lambda layer `guetteur-deps` | `opencv-python-headless==5.0.0.93` + `numpy==2.5.4`, built for arm64 | free (storage only) |
| API Gateway HTTP API | HTTPS front door, throttled to 5 req/s (burst 10), JSON access logs | pay per request |
| DynamoDB on-demand, TTL | approvals and evidence bundles; everything expires | pay per request |
| S3 bucket (7-day lifecycle, private, SSE) | opt-in depth snippets kept only after a human approved an escalation | pennies |
| CloudWatch alarms + SNS + Budget | errors, throttles, spend at 50% / 80% | alarms: first 10 free |

Nothing idles: no NAT gateway, no VPC, no EC2, no load balancer, no provisioned capacity, no Marketplace subscription.

## Build and deploy (for the account owner; not run)

```bash
cd INFRA
sam validate --lint                         # offline template check
sam build --use-container                   # arm64 layer + function (needs Docker)
sam deploy --guided                         # prompts for AlertEmail, MonthlyBudgetUsd, EdgeApiKeyValue
```

After deploy: open the `JudgeUrl` output, then `curl "$JudgeUrl/healthz"` should report `opencv: 5.0.0` and `arch: aarch64`.
Tear down with `sam delete`. Everything is created by this one stack, so deleting it removes it all.

## Known gaps in this draft

* `/api/evidence` is not yet protected by the `x-edge-key` check (parameter exists, handler check is Day 8).
* The S3-triggered snippet re-check function is described in comments only.
* `DynamoStore` in `src/guetteur/cloud/store.py` is written but has never run against DynamoDB.
* No custom domain; the default `execute-api` URL is HTTPS and sufficient for judging.
