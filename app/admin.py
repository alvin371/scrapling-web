"""Operational commands. Run inside the api container:

    python -m app.admin prune-failed            # dry run: what would be deleted
    python -m app.admin prune-failed --apply    # delete failed RQ jobs older than RQ_FAILURE_TTL
    python -m app.admin dead-list               # targets currently skipped
    python -m app.admin dead-clear threads post user/CODE
    python -m app.admin dead-clear --all        # e.g. after fixing a Threads layout change

Only RQ bookkeeping in Redis is touched; scrape_jobs rows in Postgres keep their
status/result/error (the API copies an RQ outcome into Postgres when it is polled).
"""
import argparse
import sys
import time
from datetime import datetime, timezone

from redis import Redis
from rq import Queue
from rq.job import Job
from rq.registry import FailedJobRegistry

from app.config import settings
from app.services import dedup

QUEUES = ["high", "default", "low"]


def _ended_ts(job: Job | None) -> float | None:
    if job is None or job.ended_at is None:
        return None
    ended = job.ended_at
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    return ended.timestamp()


def prune_failed(conn: Redis, older_than: int, apply: bool, batch: int = 500, now: float | None = None) -> dict:
    """Delete failed jobs that ended more than ``older_than`` seconds ago.
    Jobs whose hash already expired are dropped from the registry too.
    Newer failed jobs are kept but their Redis TTL is capped to what remains of the
    window, so they expire on their own instead of after RQ's 1-year default."""
    now = time.time() if now is None else now
    stats = {"scanned": 0, "deleted": 0, "missing_hash": 0, "kept": 0, "ttl_capped": 0}
    for name in QUEUES:
        registry = FailedJobRegistry(name, connection=conn)
        ids = [i.decode() if isinstance(i, bytes) else i for i in conn.zrange(registry.key, 0, -1)]
        for start in range(0, len(ids), batch):
            chunk = ids[start:start + batch]
            jobs = Job.fetch_many(chunk, connection=conn)
            pipe = conn.pipeline()
            for job_id, job in zip(chunk, jobs):
                stats["scanned"] += 1
                ended = _ended_ts(job)
                if job is None:
                    stats["missing_hash"] += 1
                    if apply:
                        pipe.zrem(registry.key, job_id)
                    continue
                if ended is None or now - ended >= older_than:
                    stats["deleted"] += 1
                    if apply:
                        pipe.zrem(registry.key, job_id)
                        pipe.delete(job.key, job.dependents_key, f"rq:results:{job_id}")
                    continue
                stats["kept"] += 1
                remaining = max(1, int(older_than - (now - ended)))
                if apply:
                    ttl = conn.ttl(job.key)
                    if ttl == -1 or ttl > remaining:
                        pipe.expire(job.key, remaining)
                        pipe.zadd(registry.key, {job_id: now + remaining})
                        stats["ttl_capped"] += 1
            if apply:
                pipe.execute()
    return stats


def dead_list(conn: Redis) -> list[tuple[str, int, str]]:
    out = []
    for key in conn.scan_iter(match=f"{dedup.DEAD_PREFIX}:target:*", count=500):
        k = key.decode() if isinstance(key, bytes) else key
        val = conn.get(k)
        out.append((k, conn.ttl(k), (val.decode() if isinstance(val, bytes) else val) or ""))
    return sorted(out)


def dead_clear(conn: Redis, platform: str | None, job_type: str | None, target: str | None, all_: bool) -> int:
    if all_:
        keys = list(conn.scan_iter(match=f"{dedup.DEAD_PREFIX}:*", count=500))
    else:
        dk = dedup.dead_key(platform, job_type, target)
        sk = dk.replace(":target:", ":strikes:", 1)
        keys = [dk, f"{sk}:unavailable", f"{sk}:parse"]
    return conn.delete(*keys) if keys else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("prune-failed")
    pf.add_argument("--apply", action="store_true")
    pf.add_argument("--older-than", type=int, default=settings.rq_failure_ttl, help="seconds")
    sub.add_parser("dead-list")
    dc = sub.add_parser("dead-clear")
    dc.add_argument("platform", nargs="?")
    dc.add_argument("job_type", nargs="?")
    dc.add_argument("target", nargs="?")
    dc.add_argument("--all", action="store_true")
    a = p.parse_args(argv)
    conn = Redis.from_url(settings.redis_url)

    if a.cmd == "prune-failed":
        before = {q: Queue(q, connection=conn).failed_job_registry.count for q in QUEUES}
        stats = prune_failed(conn, a.older_than, a.apply)
        print(f"{'APPLIED' if a.apply else 'DRY RUN'} at {datetime.now(timezone.utc).isoformat()} "
              f"older_than={a.older_than}s failed_before={before} {stats}")
        return 0
    if a.cmd == "dead-list":
        rows = dead_list(conn)
        for k, ttl, reason in rows:
            print(f"{k}\tttl={ttl}s\t{reason}")
        print(f"{len(rows)} dead target(s)")
        return 0
    if a.cmd == "dead-clear":
        if not a.all and not (a.platform and a.job_type and a.target):
            p.error("dead-clear needs PLATFORM JOB_TYPE TARGET or --all")
        print(f"deleted {dead_clear(conn, a.platform, a.job_type, a.target, a.all)} key(s)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
