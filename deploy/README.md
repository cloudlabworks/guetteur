# Deploy kit: AWS CloudShell, no local credentials

The deploy runs inside **AWS CloudShell** under the console session of the account owner. Nothing here needs an access key,
a profile, or a local AWS CLI, and nothing in this folder was run against an account while it was written.

## Operator steps (6)

1. Sign in to the AWS console as the account owner, switch the region to **US West (Oregon) us-west-2**, and open **CloudShell** (the `>_` icon in the top bar).
2. **Actions > Upload file** and choose `guetteur-deploy.tar.gz` (built by `deploy/make-artifact.sh`, found in `dist/`).
3. `tar xzf guetteur-deploy.tar.gz && cd guetteur-deploy`
4. `ALERT_EMAIL=<owner email> MONTHLY_BUDGET_USD=<owner's monthly cap> bash cloudshell-deploy.sh`
   (about 3-5 minutes; non-interactive; the script stops with a clear message if an input is missing or the stack is in a failed state)
5. Open the email from AWS Notifications sent to that address and click **Confirm subscription**. Alarms and budget alerts reach nobody until this is done.
6. Open the `Judge URL` printed at the end (also saved to `~/guetteur-judge-url.txt`) and send it back.

Teardown, whenever: `sam delete --stack-name guetteur-team-drift --region us-west-2 --no-prompts` (one stack owns everything).

## What the script does

| Phase | What |
| --- | --- |
| 0 | validates `ALERT_EMAIL` and `MONTHLY_BUDGET_USD` (no default for the budget; the owner chooses it) |
| 1 | checks `SHA256SUMS` and that the OpenCV layer is an ELF aarch64 build |
| 2 | uses CloudShell's `sam` (installs it with `pip --user` if absent); confirms the session is signed in |
| 3 | refuses to touch a stack stuck in `ROLLBACK_COMPLETE` and friends, and says how to delete it |
| 4 | reads the account's Lambda concurrency quota; reserves 5 if the account can, otherwise sets 0 and warns that only the API throttle caps load |
| 5 | `sam deploy` with explicit parameters, `CAPABILITY_IAM`, `--resolve-s3` (a managed bucket with public access blocked), `--no-confirm-changeset`, tags |
| 6 | `GET /healthz` must report `opencv 5.0.0` on `aarch64`; `POST /api/run` runs `subtle_item_removed` end to end on Lambda and checks that its receipts verify |
| 7 | read-only audit: reserved concurrency, log retention, S3 buckets owned by the stack (expect none), API throttling |

The artifact carries a **prebuilt arm64 build** (`build/`), so CloudShell needs neither Docker nor Python 3.12. The source
template is included for review (`INFRA/template.yaml`, same file the build came from).

## Guardrails in the template (`INFRA/template.yaml`)

| Guardrail | Setting |
| --- | --- |
| Concurrency cap | `ApiReservedConcurrency` (default 5) on `guetteur-api`; script lowers to 0 only when the account quota cannot fit it |
| API throttling | HTTP API default route: 5 requests/s, burst 10 |
| Log retention | 7 days on both log groups (`/aws/lambda/guetteur-api`, API access log), set by `LogRetentionDays` |
| No public S3 | the stack creates no bucket (the phase-2 bucket is commented out and has a full public-access block); `--resolve-s3` uses SAM's managed, private bucket |
| Spend guard | AWS Budget with e-mail alerts at 50% and 80% of the owner's monthly cap; alarms on Lambda errors and throttles |
| Public surface | read-only routes only; scenario names and small JSON in, no uploads, no visitor-chosen paths |

`deploy/check_guardrails.py` fails the artifact build if any of these is removed from the template.

## Rebuild the artifact (dev machine)

```bash
bash deploy/make-artifact.sh      # needs uv; runs guardrail check, cfn-lint, sam validate --lint, sam build, scans, then tars to dist/
```

## Needed permissions

The console user must be able to create CloudFormation stacks with IAM roles, Lambda, API Gateway, CloudWatch Logs and Alarms,
SNS, S3 (for the managed deploy bucket) and Budgets. An account owner or administrator can.
