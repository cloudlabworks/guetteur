#!/usr/bin/env bash
# Build dist/guetteur-deploy.tar.gz (the file the operator uploads to CloudShell). Runs on a dev machine, never touches AWS.
#   needs: uv (provides Python 3.12 + the SAM CLI via uvx), tar, shasum
#   usage: bash deploy/make-artifact.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$ROOT/dist/stage/guetteur-deploy"
TARBALL="$ROOT/dist/guetteur-deploy.tar.gz"
export SAM_CLI_TELEMETRY=0 AWS_EC2_METADATA_DISABLED=true
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE      # nothing here may reach an account
sam() { uvx --python 3.12 --from aws-sam-cli sam "$@"; }

echo "== guardrail check on the template"
uvx --with pyyaml --python 3.12 python -I "$ROOT/deploy/check_guardrails.py" "$ROOT/INFRA/template.yaml"
uvx cfn-lint "$ROOT/INFRA/template.yaml"
(cd "$ROOT/INFRA" && sam validate --lint --region us-west-2)

echo "== build (arm64 layer, python3.12; needs a python3.12 with pip on PATH)"
PYBIN="$(mktemp -d)"; trap 'rm -rf "$PYBIN"' EXIT
uv venv --seed --python 3.12 "$PYBIN/venv" >/dev/null
(cd "$ROOT/INFRA" && rm -rf .aws-sam && PATH="$PYBIN/venv/bin:$PATH" sam build --region us-west-2)
BUILD="$ROOT/INFRA/.aws-sam/build"

echo "== layer must be ELF aarch64 and under the 250 MB unzipped limit"
file "$BUILD/DepsLayer/python/cv2/cv2.abi3.so" | grep -q "ELF 64-bit LSB.*ARM aarch64" || { echo "layer is not aarch64"; exit 1; }
KB=$(du -sk "$BUILD" | cut -f1); [ "$KB" -lt 250000 ] || { echo "build is ${KB} KB, over the Lambda limit"; exit 1; }

echo "== stage"
rm -rf "$ROOT/dist/stage"; mkdir -p "$STAGE/build" "$STAGE/INFRA/layer"
cp "$ROOT/deploy/cloudshell-deploy.sh" "$ROOT/deploy/README.md" "$STAGE/"
cp -R "$BUILD/template.yaml" "$BUILD/ApiFunction" "$BUILD/DepsLayer" "$STAGE/build/"      # build.toml is dev-machine state, left out
cp "$ROOT/INFRA/template.yaml" "$STAGE/INFRA/"; cp "$ROOT/INFRA/layer/requirements.txt" "$STAGE/INFRA/layer/"   # reviewable source of the build
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
chmod +x "$STAGE/cloudshell-deploy.sh"
(cd "$STAGE" && find . -type f ! -name SHA256SUMS | LC_ALL=C sort | xargs shasum -a 256 | sed 's#  \./#  #' > SHA256SUMS)

echo "== privacy scan of the staged files (no home paths, hostnames, addresses)"
SCAN='/Users/|/home/[a-z]|[a-z0-9-]\.local\b|\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+(\.[0-9]+)?\b|([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}'
if grep -rIE "$SCAN" "$STAGE" --exclude-dir=numpy --exclude-dir=cv2 --exclude-dir=numpy.libs --exclude-dir='opencv_python_headless*' --exclude-dir='numpy-*' --exclude-dir='*.dist-info' | grep -vE 'from \.cloud\.local import'; then
  echo "scan hit above - fix before shipping"; exit 1
fi

echo "== tar"
mkdir -p "$ROOT/dist"; COPYFILE_DISABLE=1 tar -C "$ROOT/dist/stage" -czf "$TARBALL" guetteur-deploy
tar tzf "$TARBALL" | grep -E '(^|/)\._|\.DS_Store|build\.toml' && { echo "stray files in tarball"; exit 1; } || true
ls -l "$TARBALL"; shasum -a 256 "$TARBALL"
