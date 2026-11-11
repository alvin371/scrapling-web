"""API-side tests against a throwaway Postgres (init.sql schema) and Redis.
Run inside the api image:  pytest -q /tests/api
Env (set by the runner): DB_HOST, POSTGRES_*, REDIS_HOST, REDIS_DB, API_KEYS=testkey
"""
import asyncio
import time
import uuid

import httpx
import pytest
from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import text

from app.config import settings
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.account import Platform
from app.models.job import JobStatus, JobType
from app.schemas.job import JobCreate
from app.services import dedup
from app.services.job_service import JobService
from app import admin

LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


@pytest.fixture()
def conn():
    c = Redis.from_url(settings.redis_url)
    c.flushdb()
    run(_truncate())
    yield c
    c.flushdb()


async def _truncate():
    async with AsyncSessionLocal() as s:
        await s.execute(text("TRUNCATE scrape_jobs"))
        await s.commit()


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(settings, "skip_dead_targets", True)
    monkeypatch.setattr(settings, "dedup_inflight_jobs", True)
    monkeypatch.setattr(settings, "dedup_claim_wait_seconds", 10.0)


def post(target="alice/C1", queue="default", job_type=JobType.post, platform=Platform.threads):
    return JobCreate(platform=platform, job_type=job_type, target=target, queue=queue)


async def _enqueue(data, conn):
    async with AsyncSessionLocal() as s:
        return await JobService(s, None).enqueue(data, Queue(data.queue, connection=conn))


def enqueue(data, conn):
    return run(_enqueue(data, conn))


async def _count_rows(where=""):
    async with AsyncSessionLocal() as s:
        return (await s.execute(text(f"SELECT count(*) FROM scrape_jobs {where}"))).scalar()


def rows(where=""):
    return run(_count_rows(where))


def queued(conn, q="default"):
    return Queue(q, connection=conn).count


def set_status(conn, rq_job_id, status):
    job = Job.fetch(rq_job_id, connection=conn)
    if status == "finished":
        Queue("default", connection=conn).remove(job)
        job.set_status("finished")
    elif status == "failed":
        Queue("default", connection=conn).remove(job)
        job.set_status("failed")
    elif status == "started":
        Queue("default", connection=conn).remove(job)
        job.set_status("started")


# ------------------------------------------------------------- RQ lifetimes ---
def test_33_enqueue_sets_timeout_result_and_failure_ttl(conn):
    r = enqueue(post(), conn)
    job = Job.fetch(r.rq_job_id, connection=conn)
    assert (job.timeout, job.result_ttl, job.failure_ttl) == (300, 6 * 3600, 3 * 86400)


# ------------------------------------------------------------- dedup basics ---
def test_34_same_target_while_queued_reuses_job(conn):
    a, b = enqueue(post(), conn), enqueue(post(), conn)
    assert a.id == b.id and queued(conn) == 1 and rows() == 1


def test_35_different_targets_are_separate_jobs(conn):
    a, b = enqueue(post("alice/C1"), conn), enqueue(post("alice/C2"), conn)
    assert a.id != b.id and queued(conn) == 2


def test_36_same_target_other_queue_is_separate(conn):
    a, b = enqueue(post(queue="default"), conn), enqueue(post(queue="high"), conn)
    assert a.id != b.id and queued(conn, "default") == 1 and queued(conn, "high") == 1


def test_37_same_target_other_job_type_is_separate(conn):
    a = enqueue(post("alice", job_type=JobType.posts_detail), conn)
    b = enqueue(post("alice", job_type=JobType.profile), conn)
    assert a.id != b.id


def test_38_target_is_case_sensitive(conn):
    """Post codes are case-sensitive on Threads; never merge AbC with abc."""
    a, b = enqueue(post("alice/AbC"), conn), enqueue(post("alice/abc"), conn)
    assert a.id != b.id


@pytest.mark.parametrize("status", ["finished", "failed"])
def test_39_finished_or_failed_job_is_not_reused(conn, status):
    a = enqueue(post(), conn)
    set_status(conn, a.rq_job_id, status)
    b = enqueue(post(), conn)
    assert a.id != b.id and queued(conn) == 1


def test_40_running_job_is_reused(conn):
    a = enqueue(post(), conn)
    set_status(conn, a.rq_job_id, "started")
    assert enqueue(post(), conn).id == a.id and queued(conn) == 0


def test_41_expired_rq_job_is_not_reused(conn):
    a = enqueue(post(), conn)
    conn.delete(f"rq:job:{a.rq_job_id}")
    assert enqueue(post(), conn).id != a.id


def test_42_garbage_inflight_value_is_ignored(conn):
    conn.set(dedup.inflight_key("threads", "post", "alice/C1", "default"), "not-a-uuid")
    r = enqueue(post(), conn)
    assert r.status == JobStatus.pending and queued(conn) == 1


def test_43_inflight_pointing_to_unknown_db_row_is_ignored(conn):
    conn.set(dedup.inflight_key("threads", "post", "alice/C1", "default"), str(uuid.uuid4()))
    assert enqueue(post(), conn).status == JobStatus.pending and queued(conn) == 1


def test_44_db_terminal_status_wins_over_rq(conn):
    a = enqueue(post(), conn)

    async def mark():
        async with AsyncSessionLocal() as s:
            await s.execute(text("UPDATE scrape_jobs SET status='completed' WHERE id=:i"), {"i": a.id})
            await s.commit()
    run(mark())
    assert enqueue(post(), conn).id != a.id


def test_45_inflight_key_holds_job_id_with_ttl(conn):
    r = enqueue(post(), conn)
    k = dedup.inflight_key("threads", "post", "alice/C1", "default")
    assert conn.get(k).decode() == str(r.id)
    assert 6 * 3600 - 5 <= conn.ttl(k) <= 6 * 3600


def test_46_dedup_flag_off_restores_old_behaviour(conn, monkeypatch):
    monkeypatch.setattr(settings, "dedup_inflight_jobs", False)
    a, b = enqueue(post(), conn), enqueue(post(), conn)
    assert a.id != b.id and queued(conn) == 2 and conn.keys(f"{dedup.INFLIGHT_PREFIX}:*") == []


# ------------------------------------------------------ dedup concurrency ---
def test_47_concurrent_same_target_in_one_event_loop(conn):
    """50 simultaneous requests in the SAME uvicorn process -> 1 job, no stall."""
    async def many():
        return await asyncio.gather(*[_enqueue(post(), conn) for _ in range(50)])
    t0 = time.monotonic()
    res = run(many())
    assert len({r.id for r in res}) == 1 and queued(conn) == 1 and rows() == 1
    assert time.monotonic() - t0 < 5, "lock waits must not block the event loop"


def test_48_concurrent_mixed_targets(conn):
    async def many():
        return await asyncio.gather(*[_enqueue(post(f"u/C{i % 10}"), conn) for i in range(100)])
    res = run(many())
    assert len({r.id for r in res}) == 10 and queued(conn) == 10 and rows() == 10


def test_49_stuck_claim_fails_open(conn, monkeypatch):
    monkeypatch.setattr(settings, "dedup_claim_wait_seconds", 0.3)
    conn.set(dedup.inflight_key("threads", "post", "alice/C1", "default"), "claim:dead-request", ex=60)
    r = enqueue(post(), conn)
    assert r.rq_job_id and queued(conn) == 1


def test_50_redis_error_during_dedup_fails_open(conn, monkeypatch):
    real_get = Redis.get
    def get(self, key, *a, **k):
        if str(key).startswith(dedup.INFLIGHT_PREFIX):
            raise ConnectionError("boom")
        return real_get(self, key, *a, **k)
    monkeypatch.setattr(Redis, "get", get)
    r = enqueue(post(), conn)
    assert r.rq_job_id and queued(conn) == 1


def test_50b_create_failure_releases_claim(conn, monkeypatch):
    from app.repositories.job_repo import JobRepo
    real = JobRepo.create
    async def broken(self, data):
        raise RuntimeError("db down")
    monkeypatch.setattr(JobRepo, "create", broken)
    with pytest.raises(RuntimeError):
        enqueue(post(), conn)
    assert conn.get(dedup.inflight_key("threads", "post", "alice/C1", "default")) is None
    monkeypatch.setattr(JobRepo, "create", real)
    assert enqueue(post(), conn).rq_job_id and queued(conn) == 1


def test_50c_200_concurrent_same_target_one_job(conn):
    async def many():
        return await asyncio.gather(*[_enqueue(post(), conn) for _ in range(200)])
    res = run(many())
    assert len({r.id for r in res}) == 1 and queued(conn) == 1 and rows() == 1


def test_50d_stale_job_replaced_by_exactly_one_request(conn):
    a = enqueue(post(), conn)
    set_status(conn, a.rq_job_id, "finished")
    async def many():
        return await asyncio.gather(*[_enqueue(post(), conn) for _ in range(50)])
    res = run(many())
    ids = {r.id for r in res}
    assert len(ids) == 1 and a.id not in ids and queued(conn) == 1 and rows() == 2


# ------------------------------------------------------------ dead targets ---
def test_51_dead_target_fails_instantly_without_enqueue(conn):
    conn.set(dedup.dead_key("threads", "post", "alice/C1"), "unavailable: gone", ex=60)
    r = enqueue(post(), conn)
    assert r.status == JobStatus.failed and r.rq_job_id is None and "Skipped" in r.error and "gone" in r.error
    assert r.finished_at is not None and queued(conn) == 0 and rows("WHERE status='failed'") == 1


def test_52_dead_flag_off_ignores_mark(conn, monkeypatch):
    monkeypatch.setattr(settings, "skip_dead_targets", False)
    conn.set(dedup.dead_key("threads", "post", "alice/C1"), "x", ex=60)
    assert enqueue(post(), conn).status == JobStatus.pending and queued(conn) == 1


def test_53_dead_mark_expiry_allows_retry(conn):
    conn.set(dedup.dead_key("threads", "post", "alice/C1"), "x", px=50)
    time.sleep(0.1)
    assert enqueue(post(), conn).status == JobStatus.pending


def test_54_dead_mark_is_per_target(conn):
    conn.set(dedup.dead_key("threads", "post", "alice/C1"), "x", ex=60)
    assert enqueue(post("alice/C2"), conn).status == JobStatus.pending


# ------------------------------------------------------------- validation ---
def test_55_bad_post_target_rejected_without_orphan_row(conn):
    from fastapi import HTTPException
    for bad in ["noslash", "/C1", "alice/"]:
        with pytest.raises(HTTPException) as e:
            enqueue(post(bad), conn)
        assert e.value.status_code == 400
    assert rows() == 0 and queued(conn) == 0


def test_56_unsupported_combo_rejected_without_orphan_row(conn):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        enqueue(post("alice", job_type=JobType.followers, platform=Platform.instagram), conn)
    assert rows() == 0


# --------------------------------------------------------------- HTTP layer ---
H = {"X-API-Key": "testkey"}


async def _http(fn):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await fn(c)


def test_57_http_post_scrape_twice_same_job_id(conn):
    async def f(c):
        a = await c.post("/api/v1/posts/scrape", json={"link": "https://www.threads.com/@alice/post/C1"}, headers=H)
        b = await c.post("/api/v1/posts/scrape", json={"link": "https://www.threads.net/@alice/post/C1/"}, headers=H)
        return a, b
    a, b = run(_http(f))
    assert a.status_code == b.status_code == 201 and a.json()["job_id"] == b.json()["job_id"]


def test_58_http_skipped_job_reads_back_failed_and_cached(conn):
    conn.set(dedup.dead_key("threads", "post", "alice/C1"), "unavailable: gone", ex=60)
    async def f(c):
        a = await c.post("/api/v1/posts/scrape", json={"link": "https://www.threads.com/@alice/post/C1"}, headers=H)
        g = await c.get(f"/api/v1/jobs/{a.json()['job_id']}", headers=H)
        return a, g
    a, g = run(_http(f))
    assert g.status_code == 200 and g.json()["status"] == "failed" and "Skipped" in g.json()["error"]
    assert conn.get(f"job:{a.json()['job_id']}") is not None


def test_59_http_poll_copies_rq_result_into_db(conn, monkeypatch):
    """Real RQ round trip (SimpleWorker in-process) -> GET /jobs/{id} persists the result."""
    from rq import SimpleWorker
    orig = JobService._resolve_task
    def resolve(self, data):
        fn, kw = orig(self, data)
        return "helpers.fake_scrape", kw
    monkeypatch.setattr(JobService, "_resolve_task", resolve)
    async def create(c):
        return await c.post("/api/v1/posts/scrape", json={"link": "https://www.threads.com/@alice/post/C1"}, headers=H)
    jid = run(_http(create)).json()["job_id"]
    SimpleWorker([Queue("default", connection=conn)], connection=conn).work(burst=True)
    async def poll(c):
        return await c.get(f"/api/v1/jobs/{jid}", headers=H)
    g = run(_http(poll)).json()
    assert g["status"] == "completed" and g["result"]["likes"] == 3
    assert rows("WHERE status='completed'") == 1


def _scrape(link="https://www.threads.com/@alice/post/C1"):
    async def f(c):
        return await c.post("/api/v1/posts/scrape", json={"link": link}, headers=H)
    return run(_http(f)).json()["job_id"]


def _get(jid):
    async def f(c):
        return await c.get(f"/api/v1/jobs/{jid}", headers=H)
    return run(_http(f))


def test_59b_poll_of_job_whose_rq_record_vanished_fails_it_as_lost(conn):
    jid = _scrape()
    conn.delete(f"rq:job:{run(_jobrow(jid))}")  # result TTL ran out before anyone polled
    g = _get(jid).json()
    assert g["status"] == "failed" and g["error"].startswith("Job lost:")
    assert rows("WHERE status='failed'") == 1
    assert _get(jid).json()["status"] == "failed"  # stays failed (and cached)


def test_59c_lost_job_is_not_reused_by_the_next_submission(conn):
    jid = _scrape()
    conn.delete(f"rq:job:{run(_jobrow(jid))}")
    _get(jid)
    again = _scrape()
    assert again != jid and _get(again).json()["status"] == "pending"
    assert Job.fetch(run(_jobrow(again)), connection=conn).get_status() == "queued"


def test_59d_redis_error_while_polling_keeps_job_pending(conn, monkeypatch):
    jid = _scrape()
    def boom(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(JobService, "_fetch_rq_job_or_raise", staticmethod(boom))
    g = _get(jid)
    assert g.status_code == 200 and g.json()["status"] == "pending"
    assert rows("WHERE status='pending'") == 1


def test_59e_queued_and_started_jobs_are_not_lost(conn):
    a = _scrape("https://www.threads.com/@alice/post/C1")
    b = _scrape("https://www.threads.com/@alice/post/C2")
    set_status(conn, run(_jobrow(b)), "started")
    assert _get(a).json()["status"] == "pending" and _get(b).json()["status"] == "running"


async def _jobrow(jid):
    async with AsyncSessionLocal() as s:
        return (await s.execute(text("SELECT rq_job_id FROM scrape_jobs WHERE id=:i"), {"i": jid})).scalar()


def test_60_http_requires_api_key(conn):
    async def f(c):
        return await c.post("/api/v1/posts/scrape", json={"link": "https://www.threads.com/@a/post/C"})
    assert run(_http(f)).status_code == 401 and queued(conn) == 0


# ------------------------------------------------------------------- admin ---
def _failed_job(conn, ended_ago, ttl=None):
    q = Queue("default", connection=conn)
    job = q.enqueue("tasks.x.y", failure_ttl=365 * 86400)
    q.remove(job)
    from datetime import datetime, timedelta, timezone
    job.ended_at = datetime.now(timezone.utc) - timedelta(seconds=ended_ago)
    job.save()
    conn.zadd(f"rq:failed:default", {job.id: time.time() + 365 * 86400})
    conn.expire(job.key, ttl or 365 * 86400)
    return job.id


def test_61_prune_dry_run_changes_nothing(conn):
    ids = [_failed_job(conn, 10 * 86400) for _ in range(5)]
    st = admin.prune_failed(conn, 3 * 86400, apply=False)
    assert st["deleted"] == 5 and conn.zcard("rq:failed:default") == 5 and all(conn.exists(f"rq:job:{i}") for i in ids)


def test_62_prune_apply_deletes_old_keeps_and_caps_recent(conn):
    old = [_failed_job(conn, 10 * 86400) for _ in range(5)]
    new = [_failed_job(conn, 3600) for _ in range(3)]
    st = admin.prune_failed(conn, 3 * 86400, apply=True)
    assert st["deleted"] == 5 and st["kept"] == 3 and st["ttl_capped"] == 3
    assert not any(conn.exists(f"rq:job:{i}") for i in old)
    for i in new:
        assert conn.exists(f"rq:job:{i}") and conn.ttl(f"rq:job:{i}") <= 3 * 86400 - 3600 + 5


def test_63_prune_drops_registry_entries_with_missing_hash(conn):
    conn.zadd("rq:failed:default", {"ghost": time.time() + 999})
    st = admin.prune_failed(conn, 3 * 86400, apply=True)
    assert st["missing_hash"] == 1 and conn.zcard("rq:failed:default") == 0


def test_64_prune_is_idempotent_and_batched(conn):
    for _ in range(1200):
        _failed_job(conn, 10 * 86400)
    assert admin.prune_failed(conn, 3 * 86400, apply=True, batch=500)["deleted"] == 1200
    assert admin.prune_failed(conn, 3 * 86400, apply=True)["deleted"] == 0


def test_65_prune_never_touches_queued_or_started_jobs(conn):
    a = enqueue(post(), conn)
    _failed_job(conn, 10 * 86400)
    admin.prune_failed(conn, 3 * 86400, apply=True)
    assert conn.exists(f"rq:job:{a.rq_job_id}") and queued(conn) == 1


def test_66_dead_list_and_clear(conn):
    conn.set(dedup.dead_key("threads", "post", "a/1"), "x", ex=60)
    conn.set(dedup.dead_key("threads", "post", "a/2"), "y", ex=60)
    conn.set("scrapling:dead:strikes:threads:post:a/1:parse", 2)
    assert len(admin.dead_list(conn)) == 2
    assert admin.dead_clear(conn, "threads", "post", "a/1", False) == 2
    assert len(admin.dead_list(conn)) == 1
    admin.dead_clear(conn, None, None, None, True)
    assert admin.dead_list(conn) == []


def test_67_pinned_versions_match_production():
    import subprocess, sys
    want = {l.split("==")[0].lower(): l.split("==")[1] for l in open("/project/app/constraints.txt").read().split() if "==" in l}
    have = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout
    have = {l.split("==")[0].lower(): l.split("==")[1] for l in have.split() if "==" in l}
    drift = {k: (v, have.get(k)) for k, v in want.items() if have.get(k) != v}
    assert not drift, drift


# ---------------------------------------------- cross-process / HTTP burst ---
def _proc_enqueue(args):
    """Runs in a separate OS process (like the 2nd uvicorn worker): own loop, own pools."""
    target, barrier_key, n = args
    import asyncio as aio
    from redis import Redis as R
    c = R.from_url(settings.redis_url)
    c.incr(barrier_key)
    while int(c.get(barrier_key)) < n:  # start all processes at the same moment
        pass
    async def one():
        from app.core.database import engine
        try:
            return str((await _enqueue(post(target), c)).id)
        finally:
            await engine.dispose()
    return aio.run(one())


def test_68a_cross_process_concurrent_same_target_one_job(conn):
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    procs = 8
    with ctx.Pool(procs) as pool:
        for i in range(15):
            ids = pool.map(_proc_enqueue, [(f"mp/T{i}", f"barrier:{i}", procs)] * procs)
            assert len(set(ids)) == 1, f"round {i}: {len(set(ids))} jobs for one target"
    assert queued(conn) == 15 and rows() == 15


def test_68b_http_burst_100_concurrent_no_errors_one_job(conn):
    async def f(c):
        return await asyncio.gather(*[c.post("/api/v1/posts/scrape", json={"link": "https://www.threads.com/@burst/post/B1"}, headers=H) for _ in range(100)])
    res = run(_http(f))
    codes = [x.status_code for x in res]
    assert codes.count(201) == 100, {c: codes.count(c) for c in set(codes)}
    assert len({x.json()["job_id"] for x in res}) == 1
