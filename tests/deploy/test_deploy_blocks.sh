#!/usr/bin/env bash
# Exercise deploy.sh's DB-wait + alembic bootstrap blocks against real containers.
# Usage: IMG=scrapling-web-api:<tag> tests/deploy/test_deploy_blocks.sh
#   D1 postgres absent          -> wait loop fails, exit 1, no alembic run
#   D2 postgres appears later   -> wait succeeds, fresh DB stamped to head
#   D3 existing DB at head      -> "existing DB -> upgrade head", stays 002
#   D4 DB at 001 (old)          -> upgrade applies 002
#   D5 alembic current errors   -> abort, NOT stamp (DB revision unchanged)
# Regression for the 2026-11-10 CI failure ("Name or service not known" during
# migrate while the stack update recreated Postgres). The pre-fix deploy.sh fails D1/D2/D5.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
IMG="${IMG:-scrapling-web-api:current}"
NET=deploytest
BLOCKS="$(mktemp)"
awk '/^# --- wait for Postgres to accept connections/,/^# --- workers/' "$REPO/deploy/deploy.sh" \
  | grep -v '^# --- workers' > "$BLOCKS"
pass=0; fail=0
ok(){ echo "PASS $1"; pass=$((pass+1)); }
bad(){ echo "FAIL $1 :: $2"; fail=$((fail+1)); }
cleanup(){ docker rm -f dt-api dt-pg >/dev/null 2>&1; }
start_api(){ docker run -d --name dt-api --network $NET -e DB_HOST=dt-pg -e POSTGRES_USER=it -e POSTGRES_PASSWORD=it \
  -e POSTGRES_DB=it -e REDIS_HOST=none -e API_KEYS=k "$IMG" >/dev/null; }
start_pg(){ docker run -d --name dt-pg --network $NET -e POSTGRES_USER=it -e POSTGRES_PASSWORD=it -e POSTGRES_DB=it \
  -v "$REPO/db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro" postgres:15-alpine -c listen_addresses='*' >/dev/null; }
blocks(){ local cid; cid=$(docker ps -q -f name=dt-api); ( set -euo pipefail; DB_WAIT_TRIES=${1:-60}; source "$BLOCKS" ) 2>&1; }
rev(){ docker exec dt-pg psql -U it -d it -tAc "select version_num from alembic_version" 2>/dev/null | tr -d '[:space:]'; }

docker network create $NET >/dev/null 2>&1; cleanup
trap 'cleanup; docker network rm $NET >/dev/null 2>&1; rm -f "$BLOCKS"' EXIT

start_api; out=$(blocks 3); rc=$?
[[ $rc -ne 0 && "$out" == *"not reachable"* && "$out" != *"head"* ]] && ok "D1 absent postgres aborts before alembic" || bad D1 "rc=$rc $(echo "$out" | tail -2)"

( sleep 10; start_pg ) & out=$(blocks 60); rc=$?; wait
[[ $rc -eq 0 && "$out" == *"postgres reachable"* && "$out" == *"fresh DB -> alembic stamp head"* && "$(rev)" == 002 ]] \
  && ok "D2 late postgres: waited, fresh DB stamped to 002" || bad D2 "rc=$rc rev=$(rev) $(echo "$out" | tail -3)"

out=$(blocks 10); rc=$?
[[ $rc -eq 0 && "$out" == *"existing DB -> alembic upgrade head"* && "$(rev)" == 002 ]] \
  && ok "D3 existing DB at head: upgrade no-op" || bad D3 "rc=$rc rev=$(rev) $(echo "$out" | tail -3)"

docker exec dt-pg psql -U it -d it -qc "update alembic_version set version_num='001'"
out=$(blocks 10); rc=$?
[[ $rc -eq 0 && "$out" == *"existing DB -> alembic upgrade head"* && "$(rev)" == 002 ]] \
  && ok "D4 DB at 001 upgraded to 002" || bad D4 "rc=$rc rev=$(rev) $(echo "$out" | tail -4)"

docker exec dt-pg psql -U it -d it -qc "update alembic_version set version_num='999bogus'"
out=$(blocks 10); rc=$?
[[ $rc -ne 0 && "$out" == *"alembic current failed"* && "$(rev)" == 999bogus ]] \
  && ok "D5 failing alembic current aborts, DB not stamped" || bad D5 "rc=$rc rev=$(rev) $(echo "$out" | tail -3)"

echo "deploy blocks: $pass passed, $fail failed"; exit $fail
