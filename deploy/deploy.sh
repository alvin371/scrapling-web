#!/usr/bin/env bash
#
# Builds the scrapling-web images ON the server and deploys the Swarm stack.
# No container registry involved — a single-node Swarm can use locally-built
# images as long as we pass `--resolve-image never`.
#
# Runs on the server (invoked over SSH by CI, or by hand). Expects:
#   - the repo synced to REPO_DIR (this script's grandparent dir by default)
#   - the app secrets already written to ENV_FILE
#
# Usage: deploy.sh [IMAGE_TAG]
set -euo pipefail

TAG="${1:-current}"
STACK="scrapling-web"

# REPO_DIR = repo root (this file lives in <repo>/deploy/deploy.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DATA_DIR="${DATA_DIR:-/home/forbes/serve/scrapling-web}"
ENV_FILE="${ENV_FILE:-/home/forbes/artifact/scrapling-web/.env}"

echo "==> repo:     $REPO_DIR"
echo "==> data dir: $DATA_DIR"
echo "==> env file: $ENV_FILE"
echo "==> tag:      $TAG"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found at $ENV_FILE" >&2
  exit 1
fi

# --- persistence dirs ---------------------------------------------------------
mkdir -p "$DATA_DIR/postgres-data" "$DATA_DIR/redis-data" "$DATA_DIR/logs"

# Full schema for the dedicated Postgres' first-boot init.
cp "$REPO_DIR/db/init.sql" "$DATA_DIR/init.sql"

# --- build images on the server ----------------------------------------------
echo "==> building api image"
docker build -f "$REPO_DIR/Dockerfile.api" \
  -t "scrapling-web-api:$TAG" -t "scrapling-web-api:current" "$REPO_DIR"

echo "==> building worker image (this pulls Chromium; may take a while)"
docker build -f "$REPO_DIR/worker/Dockerfile" \
  -t "scrapling-web-worker:$TAG" -t "scrapling-web-worker:current" "$REPO_DIR/worker"

# --- deploy the stack ---------------------------------------------------------
# Load POSTGRES_USER/PASSWORD/DB (+ anything else) from the .env so the stack
# file's ${VAR} interpolation for the postgres service resolves.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export IMAGE_TAG="$TAG" DATA_DIR ENV_FILE

echo "==> docker stack deploy $STACK"
docker stack deploy --resolve-image never -c "$REPO_DIR/docker-stack.yml" "$STACK"

# --- wait for the API to come up ---------------------------------------------
echo "==> waiting for api to report healthy"
cid=""
for _ in $(seq 1 50); do
  cid="$(docker ps -q -f "name=${STACK}_api" -f health=healthy | head -n1 || true)"
  [[ -n "$cid" ]] && break
  sleep 6
done
if [[ -z "$cid" ]]; then
  echo "ERROR: api did not become healthy" >&2
  docker service ps "${STACK}_api" --no-trunc || true
  exit 1
fi

# --- database bootstrap / migrate --------------------------------------------
# Fresh DB: init.sql already built the schema at HEAD -> stamp head.
# Existing DB: apply any newer migrations -> upgrade head.
echo "==> running database bootstrap/migrate"
docker exec "$cid" sh -c '
  cd /project
  cur=$(alembic current 2>/dev/null | tr -d "[:space:]")
  if [ -z "$cur" ]; then
    echo "fresh DB -> alembic stamp head"; alembic stamp head
  else
    echo "existing DB -> alembic upgrade head"; alembic upgrade head
  fi
'

# --- health check -------------------------------------------------------------
echo "==> health check"
curl -fsS http://127.0.0.1:18000/api/v1/health && echo
echo "==> deploy complete: $STACK ($TAG)"
