#!/usr/bin/env bash
# Guetteur (Team Drift) - one-shot deploy for AWS CloudShell.
#
# Runs under the signed-in console session's credentials. It needs no access keys and writes none.
# The artifact already contains the arm64 build (build/), so CloudShell needs no Docker and no Python 3.12.
#
#   ALERT_EMAIL=owner@example.org MONTHLY_BUDGET_USD=<your cap> bash cloudshell-deploy.sh
#
# Optional: STACK_NAME (guetteur-team-drift), REGION (us-west-2), RESERVED_CONCURRENCY (auto), LOG_RETENTION_DAYS (7).
# Teardown: sam delete --stack-name "$STACK_NAME" --region "$REGION" --no-prompts
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_NAME="${STACK_NAME:-guetteur-team-drift}"
REGION="${REGION:-us-west-2}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-7}"
export AWS_DEFAULT_REGION="$REGION" AWS_REGION="$REGION" SAM_CLI_TELEMETRY=0

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---- 0. inputs ---------------------------------------------------------------------------------------------
say "0/7 inputs"
[ -n "${ALERT_EMAIL:-}" ] || die "set ALERT_EMAIL (the owner's address for alarms and budget alerts)"
[[ "$ALERT_EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "ALERT_EMAIL does not look like an email address"
[ -n "${MONTHLY_BUDGET_USD:-}" ] || die "set MONTHLY_BUDGET_USD (the owner's monthly budget cap; no default on purpose)"
[[ "$MONTHLY_BUDGET_USD" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "MONTHLY_BUDGET_USD must be a plain number"
case "$LOG_RETENTION_DAYS" in 1|3|5|7|14|30) ;; *) die "LOG_RETENTION_DAYS must be one of 1 3 5 7 14 30" ;; esac
[ "$REGION" = "us-west-2" ] || echo "note: REGION=$REGION (the kit was written for us-west-2; the arm64 Lambda layer works in any region with Graviton Lambda)"
echo "stack=$STACK_NAME region=$REGION log_retention=${LOG_RETENTION_DAYS}d alert_email=set budget=set"

# ---- 1. artifact integrity ---------------------------------------------------------------------------------
say "1/7 artifact integrity"
cd "$HERE"
[ -f build/template.yaml ] || die "build/template.yaml missing - re-extract the tarball"
sha256sum -c --quiet SHA256SUMS || die "checksum mismatch: the tarball is damaged or edited; upload it again"
echo "all checksums match"
python3 - <<'PY'
import struct, sys
p = "build/DepsLayer/python/cv2/cv2.abi3.so"
h = open(p, "rb").read(20)
if h[:4] != b"\x7fELF" or struct.unpack("<H", h[18:20])[0] != 183:   # 183 = EM_AARCH64
    sys.exit("layer is not an ELF aarch64 build: " + p)
print("layer check: cv2 is ELF aarch64")
PY

# ---- 2. tools and identity ---------------------------------------------------------------------------------
say "2/7 tools and identity"
if ! command -v sam >/dev/null 2>&1; then
  echo "sam CLI not found; installing it for this user (pip --user)"
  python3 -m pip install --user --quiet aws-sam-cli
  export PATH="$HOME/.local/bin:$PATH"
fi
sam --version
command -v aws >/dev/null 2>&1 || die "aws CLI not found (CloudShell has it; are you in CloudShell?)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" || die "no AWS session. Run this inside CloudShell while signed in to the console."
echo "signed in to account ending ...${ACCOUNT: -4}"

# ---- 3. stack state ----------------------------------------------------------------------------------------
say "3/7 existing stack"
STATUS="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo NONE)"
echo "stack status: $STATUS"
case "$STATUS" in
  ROLLBACK_COMPLETE|ROLLBACK_FAILED|DELETE_FAILED|CREATE_FAILED)
    die "stack is in $STATUS and cannot be updated. Delete it first:  sam delete --stack-name $STACK_NAME --region $REGION --no-prompts" ;;
  REVIEW_IN_PROGRESS) echo "stack is waiting in REVIEW_IN_PROGRESS (an earlier first deploy stopped at its changeset); sam deploy continues from here" ;;
  *_IN_PROGRESS)
    die "a stack operation is still running ($STATUS); wait for it to finish and run again" ;;
esac

# ---- 4. reserved-concurrency guardrail ---------------------------------------------------------------------
# The template reserves concurrency for the API function (cost + abuse ceiling). Lambda must keep 10 unreserved, so a
# brand-new account with a total quota of 10 cannot reserve any. Fall back to 0 (no reservation) loudly; API throttling stays.
say "4/7 concurrency cap"
if [ -z "${RESERVED_CONCURRENCY:-}" ]; then
  TOTAL="$(aws lambda get-account-settings --query 'AccountLimit.ConcurrentExecutions' --output text)"
  [[ "$TOTAL" =~ ^[0-9]+$ ]] || die "could not read the Lambda concurrency quota (got: $TOTAL); set RESERVED_CONCURRENCY=5 or 0 yourself"
  if [ "$TOTAL" -ge 15 ]; then RESERVED_CONCURRENCY=5; else RESERVED_CONCURRENCY=0; fi
  echo "account concurrency quota: $TOTAL -> reserved concurrency $RESERVED_CONCURRENCY"
  [ "$RESERVED_CONCURRENCY" -gt 0 ] || echo "WARNING: quota too small to reserve concurrency. Only the API Gateway throttle (5 req/s, burst 10) caps load until the quota is raised."
fi
[[ "$RESERVED_CONCURRENCY" =~ ^[0-9]+$ ]] || die "RESERVED_CONCURRENCY must be a whole number"

# ---- 5. deploy ---------------------------------------------------------------------------------------------
say "5/7 sam deploy (non-interactive)"
sam deploy \
  --template-file build/template.yaml \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --tags project=guetteur team=drift managed-by=sam \
  --parameter-overrides \
      "AlertEmail=$ALERT_EMAIL" \
      "MonthlyBudgetUsd=$MONTHLY_BUDGET_USD" \
      "ApiReservedConcurrency=$RESERVED_CONCURRENCY" \
      "LogRetentionDays=$LOG_RETENTION_DAYS"

out() { aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
JUDGE_URL="$(out JudgeUrl)"; HEALTH_URL="$(out HealthUrl)"
[ -n "$JUDGE_URL" ] && [ "$JUDGE_URL" != "None" ] || die "stack has no JudgeUrl output"

# ---- 6. smoke test -----------------------------------------------------------------------------------------
say "6/7 smoke test"
ok=""
for _ in 1 2 3 4 5 6; do
  BODY="$(curl -fsS --max-time 30 "$HEALTH_URL" 2>/dev/null || true)"
  if [[ "$BODY" == *'"opencv": "5.0.0"'* && "$BODY" == *'"arch": "aarch64"'* ]]; then ok=1; break; fi
  sleep 5
done
[ -n "$ok" ] || die "/healthz did not report opencv 5.0.0 on aarch64 (last body: ${BODY:-none}). Logs: aws logs tail /aws/lambda/guetteur-api --since 10m --region $REGION"
echo "healthz: $BODY"
RUN="$(curl -fsS --max-time 30 -X POST "${JUDGE_URL}api/run" -H 'content-type: application/json' -d '{"scenario":"subtle_item_removed","seed":0}')"
python3 - "$RUN" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
assert d["synthetic"] is True and d["score"]["success"] is True, d["score"]
assert all(r["proven"] for r in d["receipts"].values()), "a receipt did not verify"
print("api/run subtle_item_removed: success, receipts proven, server_total_ms =", d["runtime_ms"]["server_total"])
PY

# ---- 7. guardrail audit (read-only) ------------------------------------------------------------------------
say "7/7 guardrail audit"
FN="$(aws cloudformation describe-stack-resources --stack-name "$STACK_NAME" --logical-resource-id ApiFunction --query 'StackResources[0].PhysicalResourceId' --output text)"
RC="$(aws lambda get-function-concurrency --function-name "$FN" --query ReservedConcurrentExecutions --output text 2>/dev/null || echo none)"
echo "reserved concurrency on $FN: $RC"
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/guetteur-api --query 'logGroups[].[logGroupName,retentionInDays]' --output text
BUCKETS="$(aws cloudformation list-stack-resources --stack-name "$STACK_NAME" --query "StackResourceSummaries[?ResourceType=='AWS::S3::Bucket'].LogicalResourceId" --output text)"
echo "S3 buckets owned by this stack: ${BUCKETS:-none}"
API_ID="$(echo "$JUDGE_URL" | sed -E 's#https://([^.]+)\..*#\1#')"
echo "API default route throttling: $(aws apigatewayv2 get-stages --api-id "$API_ID" --query 'Items[0].DefaultRouteSettings' --output json | tr -d '\n ')"

cat <<EOF

DONE.
  Judge URL : $JUDGE_URL
  Health    : $HEALTH_URL
  Next      : open the email AWS SNS sent to $ALERT_EMAIL and click "Confirm subscription"
              (alarms and the budget alert reach nobody until you do).
  Teardown  : sam delete --stack-name $STACK_NAME --region $REGION --no-prompts
EOF
printf '%s\n' "$JUDGE_URL" > "$HOME/guetteur-judge-url.txt"
echo "(URL also saved to ~/guetteur-judge-url.txt)"
