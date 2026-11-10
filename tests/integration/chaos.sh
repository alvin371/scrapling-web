#!/usr/bin/env bash
# Chaos / lifecycle checks on the isolated stack (needs docker on the host).
# Usage: tests/integration/chaos.sh   (run after `docker compose -f compose.yml up -d --scale worker=2`)
set -uo pipefail
cd "$(dirname "$0")"
DC="docker compose -f compose.yml"
pass=0; fail=0
RUN=r$(date +%s%N)   # unique token suffix per run: no state leaks between runs
ok()  { echo "PASS $1"; pass=$((pass+1)); }
bad() { echo "FAIL $1 :: $2"; fail=$((fail+1)); }
rc()  { $DC exec -T redis redis-cli "$@"; }
enq() { # token sleep
  $DC exec -T api python -c "
from redis import Redis; from rq import Queue
Queue('default', connection=Redis(host='redis')).enqueue('tasks.probe_tasks.probe', token='$1', sleep=$2, job_timeout=300)"
}
workers() { $DC ps -q worker; }
find_runner() { # container whose RQ log shows it picked up the job with token $1
  local c; for c in $(workers); do docker logs "$c" 2>&1 | grep -qE "probe\(.*token='$1'" && { echo "$c"; return; }; done; }
wait_for() { # cmd timeout
  local end=$((SECONDS+$2)); while (( SECONDS < end )); do eval "$1" && return 0; sleep 0.5; done; return 1; }

# 75: SIGTERM during a running job -> warm shutdown: job finishes, not re-run, worker exits 0
enq warm1$RUN 8
wait_for '[ "$(rc get it:started:warm1$RUN)" = 1 ]' 30 || bad 75 "job never started"
victim=$(find_runner warm1$RUN)
[ -n "$victim" ] || { bad 75 "could not find worker running warm1$RUN"; exit 1; }
echo "  75: stopping $(docker inspect -f '{{.Name}}' "$victim") while warm1$RUN runs"
docker stop -t 60 "$victim" >/dev/null
code=$(docker inspect -f '{{.State.ExitCode}}' "$victim")
done_=$(rc get it:done:warm1$RUN); execs=$(rc get it:exec:warm1$RUN)
[ "$done_" = 1 ] && [ "$execs" = 1 ] && [ "$code" = 0 ] && ok "75 warm shutdown finished the running job (exit=$code)" || bad 75 "done=$done_ execs=$execs exit=$code"
docker start "$victim" >/dev/null

# 76: SIGKILL (OOM-like) during a job -> that job is NOT silently executed twice; others continue
wait_for "[ \$($DC exec -T api python -c \"from redis import Redis; from rq import Worker; print(len(Worker.all(connection=Redis(host='redis'))))\") -ge 2 ]" 60
enq kill1$RUN 20
wait_for '[ "$(rc get it:started:kill1$RUN)" = 1 ]' 30
victim=$(find_runner kill1$RUN)
[ -n "$victim" ] || { bad 76 "could not find worker running kill1$RUN"; exit 1; }
echo "  76: SIGKILL $(docker inspect -f '{{.Name}}' "$victim") while kill1$RUN runs"
docker kill "$victim" >/dev/null
[ "$(docker inspect -f '{{.State.Running}}' "$victim")" = false ] || bad 76 "victim still running"
for i in $(seq 1 20); do enq after$i$RUN 0; done
wait_for '[ "$(rc get it:done:after20$RUN)" = 1 ]' 60
n_after=0; for i in $(seq 1 20); do [ "$(rc get it:exec:after$i$RUN)" = 1 ] && n_after=$((n_after+1)); done
sleep 25
k=$(rc get it:exec:kill1$RUN)
[ "$k" = 1 ] && [ "$n_after" = 20 ] && ok "76 killed job ran once, 20 later jobs ran exactly once on survivor" || bad 76 "kill1$RUN execs=$k after_ok=$n_after"
docker start "$victim" >/dev/null

# 77: no log files are written by the workers (bloat regression)
files=$($DC exec -T worker sh -c 'find /worker/logs -type f | wc -l' | tail -1 | tr -d '\r')
[ "$files" = 0 ] && ok "77 no log files written under /worker/logs" || bad 77 "$files files"

# 78: no Chromium processes are ever started (THREADS_BROWSER_VIEWS=false, session set)
chrome=0; for c in $(workers); do n=$(docker top "$c" -eo args 2>/dev/null | grep -ciE 'chrom|headless_shell' || true); chrome=$((chrome+n)); done
[ "$chrome" = 0 ] && ok "78 no chromium processes in any replica" || bad 78 "$chrome chromium processes"

# 79: per-replica memory stays small after the load tests
maxmb=0; for c in $(workers); do
  mb=$(docker stats --no-stream --format '{{.MemUsage}}' "$c" | awk '{v=$1; if (v ~ /GiB/) {sub(/GiB/,"",v); v*=1024} else {sub(/MiB/,"",v)}; printf "%d", v}')
  (( mb > maxmb )) && maxmb=$mb; done
(( maxmb < 300 )) && ok "79 max replica memory ${maxmb}MiB < 300MiB" || bad 79 "${maxmb}MiB"

# 80: each line logged once (no duplicate handler)
c=$(workers | head -1)
dups=$(docker logs "$c" 2>&1 | grep -E '\| INFO ' | sort | uniq -d | wc -l)
[ "$dups" = 0 ] && ok "80 no duplicated log lines" || bad 80 "$dups duplicated lines"

echo "chaos: $pass passed, $fail failed"
exit $fail
