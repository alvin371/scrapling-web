"""Integration tests against the isolated stack in compose.yml (2 worker replicas).
Runs in a container on the stack's network:  pytest -q /tests/integration -p no:cacheprovider
LIVE=1 enables the tests that hit real Threads.
"""
import asyncio
import os
import time
import uuid

import httpx
import pytest
from redis import Redis
from rq import Queue, Worker

API = "http://api:8000/api/v1"
H = {"X-API-Key": "itkey"}
r = Redis(host="redis", port=6379)
LIVE = os.environ.get("LIVE") == "1"

# Real posts that scraped fine on 2026-11-10, and ones Threads reports as invalid.
GOOD = [l.split() for l in open("/exp/sample.txt")] if os.path.exists("/exp/sample.txt") else []
GOOD = [(u, c) for u, c in GOOD if u not in ("cheezystrawcake", "cindyoctaviii")]
INVALID = ("cheezystrawcake", "DebfrKVEwcF")


def wait(pred, timeout=120, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def poll_job(jid, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        j = httpx.get(f"{API}/jobs/{jid}", headers=H, timeout=10).json()
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(1)
    raise AssertionError(f"job {jid} not finished")


def link(u, c):
    return f"https://www.threads.com/@{u}/post/{c}"


@pytest.fixture(autouse=True, scope="module")
def ready():
    assert wait(lambda: httpx.get(f"{API}/health", timeout=5).json()["db"] == "connected", 60)
    assert wait(lambda: len(Worker.all(connection=r)) == 2, 60), "expected 2 workers"


def test_68_two_workers_registered_with_unique_names():
    names = [w.name for w in Worker.all(connection=r)]
    assert len(names) == len(set(names)) == 2


def test_69_500_jobs_each_executed_exactly_once_by_both_workers():
    r.delete("it:who")
    q = Queue("default", connection=r)
    tokens = [uuid.uuid4().hex for _ in range(500)]
    for t in tokens:
        q.enqueue("tasks.probe_tasks.probe", token=t, result_ttl=600)
    assert wait(lambda: all(r.exists(f"it:done:{t}") for t in tokens[-20:]) and q.count == 0, 300)
    assert wait(lambda: sum(1 for t in tokens if r.exists(f"it:done:{t}")) == 500, 60)
    counts = [int(r.get(f"it:exec:{t}") or 0) for t in tokens]
    assert counts.count(1) == 500, f"duplicates/lost: {[c for c in counts if c != 1][:10]}"
    hosts = {x.decode().split("|")[1] for x in r.lrange("it:who", 0, -1)}
    assert len(hosts) == 2, f"both replicas must take work, got {hosts}"


def test_70_http_50_concurrent_same_link_one_job():
    l = link("ituser", f"C{uuid.uuid4().hex[:8]}")
    before = Queue("default", connection=r).count

    async def go():
        async with httpx.AsyncClient(timeout=30) as c:
            return await asyncio.gather(*[c.post(f"{API}/posts/scrape", json={"link": l}, headers=H) for _ in range(50)])
    res = asyncio.run(go())
    assert all(x.status_code == 201 for x in res)
    ids = list(dict.fromkeys(x.json()["job_id"] for x in res))
    # Live workers may finish (fail) the fake post while the burst is still arriving;
    # then a NEW job is correct. The invariant: never two jobs in flight at once.
    jobs = sorted((poll_job(j) for j in ids), key=lambda j: j["created_at"])
    for prev, nxt in zip(jobs, jobs[1:]):
        assert prev["finished_at"] and nxt["created_at"] >= prev["finished_at"], (prev, nxt)
    assert len(jobs) <= 3, f"{len(jobs)} jobs for one link in one burst"


def test_71_http_concurrent_distinct_links_no_merge():
    links = [link("ituser", f"D{uuid.uuid4().hex[:8]}") for _ in range(30)]

    async def go():
        async with httpx.AsyncClient(timeout=30) as c:
            return await asyncio.gather(*[c.post(f"{API}/posts/scrape", json={"link": l}, headers=H) for l in links * 2])
    res = asyncio.run(go())
    assert all(x.status_code == 201 for x in res)
    by_link = {}
    for l, x in zip(links * 2, res):
        by_link.setdefault(l, []).append(x.json()["job_id"])
    all_ids = [i for ids in by_link.values() for i in ids]
    # No job is ever shared between two different links...
    owners = {}
    for l, ids in by_link.items():
        for i in ids:
            assert owners.setdefault(i, l) == l, f"job {i} shared by {owners[i]} and {l}"
    # ...and per link, jobs never overlap in time (a 2nd one only after the 1st ended).
    for l, ids in by_link.items():
        jobs = sorted((poll_job(i) for i in dict.fromkeys(ids)), key=lambda j: j["created_at"])
        for prev, nxt in zip(jobs, jobs[1:]):
            assert prev["finished_at"] and nxt["created_at"] >= prev["finished_at"], (l, prev, nxt)


@pytest.mark.skipif(not LIVE or not GOOD, reason="LIVE=1 needed")
def test_72_live_posts_complete_with_views_and_no_duplicates():
    posts = GOOD[:15]

    async def submit():
        async with httpx.AsyncClient(timeout=30) as c:
            return await asyncio.gather(*[c.post(f"{API}/posts/scrape", json={"link": link(u, k)}, headers=H) for u, k in posts * 2])
    res = asyncio.run(submit())
    ids = [x.json()["job_id"] for x in res]
    assert len(set(ids)) == len(posts), "resubmitting an in-flight post must reuse its job"
    jobs = [poll_job(j) for j in set(ids)]
    bad = [(j["target"], j["error"]) for j in jobs if j["status"] != "completed"]
    assert not bad, bad
    for j in jobs:
        res_ = j["result"]
        assert res_["url"].split("/post/")[1].startswith(j["target"].split("/")[1])
        assert res_["views"] > 0 and res_["likes"] is not None


@pytest.mark.skipif(not LIVE, reason="LIVE=1 needed")
def test_73_live_invalid_post_becomes_dead_then_skipped_instantly():
    u, c = INVALID
    for k in r.scan_iter(match=f"scrapling:dead:*{u}/{c}*"):
        r.delete(k)
    errs = []
    for _ in range(2):
        jid = httpx.post(f"{API}/posts/scrape", json={"link": link(u, c)}, headers=H, timeout=30).json()["job_id"]
        j = poll_job(jid)
        assert j["status"] == "failed"
        errs.append(j["error"])
    t0 = time.time()
    jid = httpx.post(f"{API}/posts/scrape", json={"link": link(u, c)}, headers=H, timeout=30).json()["job_id"]
    j = httpx.get(f"{API}/jobs/{jid}", headers=H).json()
    assert j["status"] == "failed" and j["error"].startswith("Skipped") and time.time() - t0 < 2
    assert "ThreadsPostUnavailable" in errs[0]


def test_74_failed_and_finished_jobs_have_bounded_ttl():
    q = Queue("default", connection=r)
    jid = httpx.post(f"{API}/posts/scrape", json={"link": link("ituser", "TTL" + uuid.uuid4().hex[:6])}, headers=H).json()["job_id"]
    j = poll_job(jid)  # fake post -> fails on real Threads or network; either way terminal
    rq_id = j["rq_job_id"]
    ttl = r.ttl(f"rq:job:{rq_id}")
    assert 0 < ttl <= 3 * 86400, ttl
