# INFRA: AWS SAM template (DRAFT, not deployed)

Nothing in this folder has been deployed, and no AWS account or credential was used to write it. It exists so the
architecture in `DESIGN.md` is concrete and reviewable.

| Resource | Why | Cost shape |
| --- | --- | --- |
| `guetteur-api` Lambda, **arm64 / Graviton**, python3.12, 1769 MB (one full vCPU) | serves the dashboard and runs the OpenCV 5 loop and both receipt checks (public, read-only, synthetic scenes) | pay per request; reserved concurrency 5 caps it |
| Lambda layer `guetteur-deps` | `opencv-python-headless==5.0.0.93` + `numpy==2.5.4`, built for arm64 | free (storage only) |
| API Gateway HTTP API | HTTPS front door, throttled to 5 req/s (burst 10), JSON access logs | pay per request |
| *(phase 2, commented out)* DynamoDB on-demand + TTL, S3 7-day bucket, evidence + approval routes | authenticated edge intake, human approvals, opt-in depth snippets | pay per request |
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

## Why the first deploy is read-only

`/api/evidence` and `/api/approvals*` can release or suppress alerts, so they must not exist unauthenticated. The handler
already refuses them unless an `EDGE_KEY` (>= 20 chars) is configured and sent as `x-edge-key` (fail shut; tested locally),
but the template leaves those routes, the DynamoDB table and the S3 bucket out until a deployed negative test (wrong key -> 403) is in place.
The public routes accept only built-in scenario names and small JSON, never uploads or paths.

## Known gaps in this draft

* Phase 2 resources are commented out and the S3-triggered snippet re-check function is not written.
* `DynamoStore` in `src/guetteur/cloud/store.py` is written but has never run against DynamoDB.
* `ReservedConcurrentExecutions: 5` can be rejected on a brand-new account whose total concurrency quota is low; remove the line if the deploy says so (the API throttle still applies).
* Lambda timing is unmeasured: locally the slowest scenario takes about 2.6 s through `/api/run` (14-thread M4 Pro); Lambda will be slower, 25 s is the function timeout.
* No custom domain; the default `execute-api` URL is HTTPS and sufficient for judging.
* The repo should stay private until the first deploy has passed its checks (OCV "Final Submission Package" allows a private, judge-accessible repository).
