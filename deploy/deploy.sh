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
STACK="${STACK:-scrapling-web}"
API_PORT="${API_PORT:-18000}"

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

# --- helpers -------------------------------------------------------------------
# State of the last rolling update of a service ("" when it never had one).
update_state() { docker service inspect -f '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}' "$1" 2>/dev/null || true; }

# Wait until a service's rolling update has finished; fail on rollback/pause.
wait_service_update() { # service timeout_seconds
  local svc=$1 end=$((SECONDS + $2)) st
  while (( SECONDS < end )); do
    st=$(update_state "$svc")
    case "$st" in
      ""|completed) return 0 ;;
      updating) ;;
      *) echo "ERROR: $svc update state '$st'" >&2
         docker service inspect -f '{{.UpdateStatus.Message}}' "$svc" >&2 || true
         docker service ps "$svc" --no-trunc | head -6 >&2 || true
         return 1 ;;
    esac
    sleep 3
  done
  echo "ERROR: $svc update did not finish within $2s" >&2
  return 1
}

# Container of the api task Swarm wants running now, if it is running and healthy.
# Resolved through the Swarm task, NOT by image: when api code is unchanged two tags
# share one image ID, and `docker ps -f ancestor=` then also matches the OLD container
# Swarm is about to stop (2026-11-10: migrate exec killed with exit 137).
api_container() {
  local tid c
  for tid in $(docker service ps -q --filter desired-state=running "${STACK}_api" 2>/dev/null); do
    [[ "$(docker inspect -f '{{.Status.State}}' "$tid" 2>/dev/null)" == running ]] || continue
    c=$(docker inspect -f '{{.Status.ContainerStatus.ContainerID}}' "$tid" 2>/dev/null) || continue
    [[ -n "$c" && "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null)" == healthy ]] || continue
    echo "$c"; return 0
  done
  return 1
}

# --- wait for the API to come up ---------------------------------------------
echo "==> waiting for the api update to finish"
wait_service_update "${STACK}_api" 300 || exit 1
echo "==> waiting for api to report healthy"
cid=""
for _ in $(seq 1 50); do
  cid="$(api_container || true)"
  [[ -n "$cid" ]] && break
  sleep 6
done
if [[ -z "$cid" ]]; then
  echo "ERROR: api did not become healthy" >&2
  docker service ps "${STACK}_api" --no-trunc || true
  exit 1
fi
echo "api container: $(docker inspect -f '{{.Name}} {{.Config.Image}}' "$cid")"

# --- wait for Postgres to accept connections ---------------------------------
# The api health endpoint returns ok even when the DB is unreachable, so we must
# wait on Postgres itself before running migrations. Check FROM the api container:
# when the stack update recreates Postgres, its service name does not resolve on
# the overlay network until the new task is up (2026-11-10 deploy failed here with
# "Name or service not known").
echo "==> waiting for postgres to be reachable from the api container"
db_ok=""
for _ in $(seq 1 "${DB_WAIT_TRIES:-60}"); do
  if docker exec "$cid" python -c "
import asyncio, asyncpg
from app.config import settings as s
async def main():
    c = await asyncpg.connect(host=s.db_host, port=s.db_port, user=s.postgres_user,
                              password=s.postgres_password, database=s.postgres_db, timeout=5)
    await c.fetchval('SELECT 1'); await c.close()
asyncio.run(main())" >/dev/null 2>&1; then
    db_ok=1; echo "postgres reachable"; break
  fi
  sleep 3
done
if [[ -z "$db_ok" ]]; then
  echo "ERROR: postgres not reachable from the api container" >&2
  docker service ps "${STACK}_postgres" --no-trunc | head -5 || true
  exit 1
fi

# --- database bootstrap / migrate --------------------------------------------
# Fresh DB: init.sql already built the schema at HEAD -> stamp head.
# Existing DB: apply any newer migrations -> upgrade head.
echo "==> running database bootstrap/migrate"
docker exec "$cid" sh -c '
  cd /project
  # A failing `alembic current` must abort, never be mistaken for a fresh DB
  # (stamping an existing DB would silently skip its pending migrations).
  if ! out=$(alembic current 2>&1); then
    echo "ERROR: alembic current failed:" >&2; echo "$out" | tail -5 >&2; exit 1
  fi
  cur=$(printf "%s" "$out" | grep -E "^[0-9a-f]+" | tr -d "[:space:]")
  if [ -z "$cur" ]; then
    echo "fresh DB -> alembic stamp head"; alembic stamp head
  else
    echo "existing DB -> alembic upgrade head"; alembic upgrade head
  fi
'

# --- workers -----------------------------------------------------------------
# The worker update is rolling (one replica at a time, each finishing its running
# job first: stop_grace_period 330s), so it can outlast the api. Report success
# only once it has converged; a rollback fails the deploy.
echo "==> waiting for the worker update to finish"
wait_service_update "${STACK}_worker" 900 || exit 1

# --- health check -------------------------------------------------------------
echo "==> health check"
curl -fsS "http://127.0.0.1:${API_PORT}/api/v1/health" && echo
echo "==> deploy complete: $STACK ($TAG)"
