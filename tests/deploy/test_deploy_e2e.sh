#!/usr/bin/env bash
# End-to-end test of deploy/deploy.sh on the local Swarm, in a SEPARATE stack
# (default "scrdt", ports 28000/29181, throwaway data dir) — production is untouched.
#
#   E1 fresh stack                          -> deploy ok, fresh DB stamped to head
#   E2 redeploy, IDENTICAL images, new tag  -> deploy ok (2026-11-10: exit 137, the
#                                              migrate exec ran in the old container)
#   E3 same as E2 with deploy.sh from OLD_REF (optional, expected to FAIL)
#   E4 redeploy with a CHANGED api image    -> deploy ok, migrate ran in the new image
# After every successful deploy: api healthy on its port, worker 2/2 on the new tag,
# both updates "completed", DB at alembic head.
#
# Usage: tests/deploy/test_deploy_e2e.sh [OLD_REF]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
export STACK="${STACK:-scrdt}" API_PORT="${API_PORT:-28000}" RQ_DASHBOARD_PORT="${RQ_DASHBOARD_PORT:-29181}"
WORK="$(mktemp -d)"; export DATA_DIR="$WORK/data" ENV_FILE="$WORK/app.env" REPO_DIR="$WORK/repo"
OLD_REF="${1:-}"
[[ "$STACK" == scrapling-web ]] && { echo "refusing to test against the production stack"; exit 2; }
pass=0; fail=0
ok(){ echo "PASS $1"; pass=$((pass+1)); }
bad(){ echo "FAIL $1 :: $2"; fail=$((fail+1)); }

cat > "$ENV_FILE" <<EOF
POSTGRES_USER=dt
POSTGRES_PASSWORD=dt
POSTGRES_DB=dt
API_KEYS=k
APP_ENV=production
EOF
cp -a "$REPO" "$REPO_DIR"; rm -rf "$REPO_DIR/.git"

teardown() {
  docker stack rm "$STACK" >/dev/null 2>&1
  for _ in $(seq 1 60); do [[ -z "$(docker ps -aq -f "label=com.docker.stack.namespace=$STACK")" ]] && break; sleep 2; done
  for _ in $(seq 1 30); do docker network inspect "${STACK}_default" >/dev/null 2>&1 || break; sleep 2; done
  # postgres-data is owned by the container's uid: remove it from a container
  docker run --rm -v "$WORK:/w" alpine sh -c 'rm -rf /w/data' >/dev/null 2>&1
  rm -rf "$WORK"
  docker image rm $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E ':(e2e-)') >/dev/null 2>&1 || true
}
# deploy.sh also tags images ":current"; restore them afterwards so the test leaves no trace.
CUR_API=$(docker image inspect -f '{{.Id}}' scrapling-web-api:current 2>/dev/null || true)
CUR_WRK=$(docker image inspect -f '{{.Id}}' scrapling-web-worker:current 2>/dev/null || true)
restore_current() {
  [[ -n "$CUR_API" ]] && docker tag "$CUR_API" scrapling-web-api:current
  [[ -n "$CUR_WRK" ]] && docker tag "$CUR_WRK" scrapling-web-worker:current
}
trap 'teardown; restore_current' EXIT

deploy() { # tag [deploy_script]
  local script="${2:-$REPO_DIR/deploy/deploy.sh}"
  bash "$script" "$1" > "$WORK/deploy-$1.log" 2>&1
}
check_converged() { # name tag
  local n=$1 tag=$2 h w au wu rev
  h=$(curl -fsS "http://127.0.0.1:${API_PORT}/api/v1/health" 2>/dev/null)
  w=$(docker service ls -f "name=${STACK}_worker" --format '{{.Replicas}} {{.Image}}')
  au=$(docker service inspect -f '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}' "${STACK}_api")
  wu=$(docker service inspect -f '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}' "${STACK}_worker")
  rev=$(docker exec "$(docker ps -q -f "name=${STACK}_postgres" | head -1)" psql -U dt -d dt -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')
  [[ "$h" == *'"db":"connected"'* && "$w" == "2/2 scrapling-web-worker:$tag" && "$au" =~ ^(|completed)$ && "$wu" =~ ^(|completed)$ && "$rev" == 002 ]] \
    && ok "$n converged: api healthy, worker $w, updates done, alembic $rev" \
    || bad "$n" "health=$h worker=$w api_upd=$au worker_upd=$wu rev=$rev"
}

# E1
if deploy e2e-1; then
  grep -q "fresh DB -> alembic stamp head" "$WORK/deploy-e2e-1.log" && ok "E1 fresh stack deployed, DB stamped" || bad E1 "no fresh-DB stamp in log"
  check_converged E1 e2e-1
else bad E1 "deploy failed: $(tail -5 "$WORK/deploy-e2e-1.log")"; fi

# E3 (optional): old deploy.sh, identical images -> expected to fail like CI did
if [[ -n "$OLD_REF" ]]; then
  git -C "$REPO" show "$OLD_REF:deploy/deploy.sh" > "$WORK/old-deploy.sh"
  sed -i -e "s#^REPO_DIR=.*#REPO_DIR=\"$REPO_DIR\"#" \
         -e "s#^STACK=.*#STACK=\"$STACK\"#" \
         -e "s#127.0.0.1:18000#127.0.0.1:${API_PORT}#g" "$WORK/old-deploy.sh"
  # SAFETY: the old script hard-codes the production stack name. Never run it unless
  # the stack line now names the test stack and nothing references scrapling-web as a stack.
  if [[ "$(grep -E '^STACK=' "$WORK/old-deploy.sh")" != "STACK=\"$STACK\"" ]] || grep -qE 'stack deploy.*scrapling-web"?$|:18000' "$WORK/old-deploy.sh"; then
    echo "SAFETY ABORT: old deploy.sh still targets production"; exit 3
  fi
  if deploy e2e-3 "$WORK/old-deploy.sh"; then
    bad "E3(control)" "old deploy.sh unexpectedly succeeded — test does not reproduce the bug"
  else
    ok "E3(control) old deploy.sh fails on identical images: $(grep -E 'Error|ERROR|exit code' "$WORK/deploy-e2e-3.log" | tail -1)"
  fi
  # let that half-finished update settle before the next deploy
  for s in api worker; do for _ in $(seq 1 100); do [[ "$(docker service inspect -f '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}' "${STACK}_$s")" == updating ]] || break; sleep 3; done; done
fi

# E2: identical images under a new tag (the 2026-11-10 failure)
a1=$(docker image inspect -f '{{.Id}}' scrapling-web-api:e2e-1)
if deploy e2e-2; then
  a2=$(docker image inspect -f '{{.Id}}' scrapling-web-api:e2e-2)
  [[ "$a1" == "$a2" ]] && ok "E2 identical api image under a new tag deployed (image ${a2:7:12})" || bad E2 "images differ, scenario not reproduced"
  grep -q "existing DB -> alembic upgrade head" "$WORK/deploy-e2e-2.log" || bad E2 "migrate step did not run"
  check_converged E2 e2e-2
else bad E2 "deploy failed: $(tail -5 "$WORK/deploy-e2e-2.log")"; fi

# E4: changed api image
echo "# e2e $(date +%s%N)" >> "$REPO_DIR/app/__init__.py"
if deploy e2e-4; then
  a4=$(docker image inspect -f '{{.Id}}' scrapling-web-api:e2e-4)
  ran_in=$(grep -E '^api container:' "$WORK/deploy-e2e-4.log" | awk '{print $NF}')
  [[ "$a4" != "$a2" && "$ran_in" == "scrapling-web-api:e2e-4" ]] && ok "E4 changed api image deployed; migrate ran in $ran_in" || bad E4 "a4=${a4:7:12} ran_in=$ran_in"
  check_converged E4 e2e-4
else bad E4 "deploy failed: $(tail -5 "$WORK/deploy-e2e-4.log")"; fi

echo "deploy e2e: $pass passed, $fail failed"
exit $fail
