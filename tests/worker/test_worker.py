"""Worker-side tests. Run inside the worker image (WORKDIR /worker) with REDIS_URL_TEST
pointing at a throwaway Redis:  pytest -q /tests/worker
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest
import redis

sys.path.insert(0, "/worker")
import dead_targets  # noqa: E402
import tasks.threads_tasks as tt  # noqa: E402
from config import settings, Settings  # noqa: E402

R_URL = os.environ.get("REDIS_URL_TEST", "redis://redis:6379/15")


# ----------------------------------------------------------------- fixtures ---
@pytest.fixture()
def r():
    conn = redis.Redis.from_url(R_URL)
    conn.flushdb()
    yield conn
    conn.flushdb()


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "threads_browser_views", False)
    monkeypatch.setattr(settings, "threads_session_id", "sess")
    monkeypatch.setattr(settings, "skip_dead_targets", True)
    monkeypatch.setattr(settings, "dead_unavailable_threshold", 2)
    monkeypatch.setattr(settings, "dead_unavailable_ttl_seconds", 7 * 86400)
    monkeypatch.setattr(settings, "dead_parse_threshold", 3)
    monkeypatch.setattr(settings, "dead_parse_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "dead_strike_window_seconds", 3 * 86400)


@pytest.fixture()
def use_conn(monkeypatch, r):
    """Pretend we run inside an RQ job whose connection is the test Redis."""
    monkeypatch.setattr(dead_targets, "_conn", lambda: r)
    return r


def ssr_doc(code, likes=5, replies=2, reposts=1, quotes=0, user="alice"):
    post = {
        "code": code, "pk": "123", "like_count": likes, "taken_at": 1_700_000_000,
        "caption": {"text": "hello"}, "media_type": 19,
        "text_post_app_info": {"direct_reply_count": replies, "repost_count": reposts, "quote_count": quotes},
        "user": {"username": user, "pk": "9", "follower_count": 42, "full_name": "Alice"},
        "canonical_url": f"https://www.threads.com/@{user}/post/{code}",
    }
    return {"require": [[0, 0, 0, [{"__bbox": {"require": [[0, 0, 0, [None, {"__bbox": {"result": {"data": {"media": post}}}}]]]}}]]]}


class FakeScript:
    def __init__(self, text):
        self.text = text


class FakePage:
    def __init__(self, scripts=(), url="https://www.threads.com/@alice/post/X", html="", status=200):
        self._scripts = [FakeScript(s) for s in scripts]
        self.url = url
        self.html_content = html
        self.status = status

    def css(self, sel):
        return self._scripts if "application/json" in sel else []


def install_page(monkeypatch, page):
    calls = []

    class FakeSession:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            calls.append(url)
            return page

    monkeypatch.setattr(tt, "FetcherSession", FakeSession)
    return calls


def ok_page(code="X", views_html="1.2K views", **kw):
    return FakePage([json.dumps(ssr_doc(code, **kw))], html=f"<span>{views_html}</span>")


def forbid_browser(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Playwright must not be launched")
    monkeypatch.setattr(tt, "_get_threads_post_views", boom)


# ------------------------------------------------------- 1. Playwright flag ---
def test_01_browser_flag_off_never_launches_browser(monkeypatch, use_conn):
    install_page(monkeypatch, ok_page())
    forbid_browser(monkeypatch)
    assert tt.scrape_threads_post("alice", "X")["views"] == 1200


def test_02_browser_flag_on_with_session_uses_browser_count(monkeypatch, use_conn):
    monkeypatch.setattr(settings, "threads_browser_views", True)
    install_page(monkeypatch, ok_page())
    monkeypatch.setattr(tt, "_get_threads_post_views", lambda url, sid: 1234)
    assert tt.scrape_threads_post("alice", "X")["views"] == 1234


def test_03_browser_flag_on_without_session_skips_browser(monkeypatch, use_conn):
    monkeypatch.setattr(settings, "threads_browser_views", True)
    monkeypatch.setattr(settings, "threads_session_id", "")
    install_page(monkeypatch, ok_page())
    forbid_browser(monkeypatch)
    assert tt.scrape_threads_post("alice", "X")["views"] == 1200


def test_04_browser_returning_zero_keeps_http_views(monkeypatch, use_conn):
    monkeypatch.setattr(settings, "threads_browser_views", True)
    install_page(monkeypatch, ok_page())
    monkeypatch.setattr(tt, "_get_threads_post_views", lambda url, sid: 0)
    assert tt.scrape_threads_post("alice", "X")["views"] == 1200


def test_05_metrics_mapped_from_http_response(monkeypatch, use_conn):
    install_page(monkeypatch, ok_page(likes=7, replies=3, reposts=2, quotes=1))
    r = tt.scrape_threads_post("alice", "X")
    assert (r["likes"], r["comments"], r["reposts"], r["quotes"]) == (7, 3, 2, 1)
    assert r["url"].endswith("/post/X") and r["followers"] == 42 and r["author_id"] == "9"


def test_06_no_views_in_html_gives_zero_not_error(monkeypatch, use_conn):
    install_page(monkeypatch, ok_page(views_html=""))
    assert tt.scrape_threads_post("alice", "X")["views"] == 0


@pytest.mark.parametrize("text,expected", [("7.6K", 7600), ("1,234", 1234), ("1.2M", 1_200_000), ("999", 999), ("", 0), ("abc", 0)])
def test_07_abbreviated_counts(text, expected):
    assert tt._parse_abbreviated_count(text) == expected


def test_08_browser_launcher_never_called_with_flag_off(monkeypatch, use_conn):
    """Patch the real launcher (not our wrapper): flag off => no browser even with a session."""
    import playwright.sync_api as pw
    def boom():
        raise AssertionError("sync_playwright() called")
    monkeypatch.setattr(pw, "sync_playwright", boom)
    install_page(monkeypatch, ok_page())
    assert tt.scrape_threads_post("alice", "X")["likes"] == 5


# --------------------------------------------- 2. failure classification ---
def test_09_invalid_post_redirect_is_unavailable(monkeypatch, use_conn):
    install_page(monkeypatch, FakePage(url="https://www.threads.com/?error=invalid_post", status=429))
    with pytest.raises(tt.ThreadsPostUnavailable):
        tt.scrape_threads_post("alice", "X")


def test_10_failure_classes_stay_valueerror_for_clients(monkeypatch, use_conn):
    assert issubclass(tt.ThreadsPostUnavailable, ValueError) and issubclass(tt.ThreadsPostParseError, ValueError)
    install_page(monkeypatch, FakePage(["{}"]))
    with pytest.raises(ValueError, match="Could not parse Threads post data"):
        tt.scrape_threads_post("alice", "X")


def test_11_other_posts_json_is_not_used(monkeypatch, use_conn):
    """A page embedding a *different* post must not be returned as this post."""
    install_page(monkeypatch, FakePage([json.dumps(ssr_doc("OTHER"))]))
    with pytest.raises(tt.ThreadsPostParseError):
        tt.scrape_threads_post("alice", "X")


def test_12_malformed_json_is_parse_error(monkeypatch, use_conn):
    install_page(monkeypatch, FakePage(['{"X": like_count broken']))
    with pytest.raises(tt.ThreadsPostParseError):
        tt.scrape_threads_post("alice", "X")


def test_13_network_error_is_not_a_strike(monkeypatch, use_conn):
    class Boom:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): raise ConnectionError("reset")
    monkeypatch.setattr(tt, "FetcherSession", Boom)
    with pytest.raises(ConnectionError):
        tt.scrape_threads_post("alice", "X")
    assert use_conn.keys("scrapling:dead:*") == []


# ------------------------------------------------- 3. dead-target strikes ---
def dead(r, target="alice/X"):
    return r.get(dead_targets.dead_key("threads", "post", target))


def test_14_one_unavailable_strike_is_not_dead(monkeypatch, use_conn):
    install_page(monkeypatch, FakePage(url="https://www.threads.com/?error=invalid_post"))
    with pytest.raises(ValueError):
        tt.scrape_threads_post("alice", "X")
    assert dead(use_conn) is None


def test_15_two_unavailable_strikes_mark_dead_for_7_days(monkeypatch, use_conn):
    install_page(monkeypatch, FakePage(url="https://www.threads.com/?error=invalid_post"))
    for _ in range(2):
        with pytest.raises(ValueError):
            tt.scrape_threads_post("alice", "X")
    assert dead(use_conn).decode().startswith("unavailable:")
    assert 7 * 86400 - 5 <= use_conn.ttl(dead_targets.dead_key("threads", "post", "alice/X")) <= 7 * 86400


def test_16_parse_errors_need_three_strikes_and_dead_for_1_day(monkeypatch, use_conn):
    install_page(monkeypatch, FakePage(["{}"]))
    for i in range(3):
        with pytest.raises(ValueError):
            tt.scrape_threads_post("alice", "X")
        if i < 2:
            assert dead(use_conn) is None
    assert dead(use_conn).decode().startswith("parse:")
    assert 86400 - 5 <= use_conn.ttl(dead_targets.dead_key("threads", "post", "alice/X")) <= 86400


def test_17_kinds_count_separately(use_conn):
    dead_targets.record_permanent_failure("threads", "post", "a/1", "unavailable", "x")
    dead_targets.record_permanent_failure("threads", "post", "a/1", "parse", "x")
    dead_targets.record_permanent_failure("threads", "post", "a/1", "parse", "x")
    assert dead(use_conn, "a/1") is None


def test_18_strike_keys_expire_after_window(use_conn):
    dead_targets.record_permanent_failure("threads", "post", "a/1", "unavailable", "x")
    ttl = use_conn.ttl(dead_targets.strike_key("threads", "post", "a/1") + ":unavailable")
    assert 3 * 86400 - 5 <= ttl <= 3 * 86400


def test_19_success_clears_strikes_and_dead_mark(monkeypatch, use_conn):
    dead_targets.record_permanent_failure("threads", "post", "alice/X", "unavailable", "x")
    use_conn.set(dead_targets.dead_key("threads", "post", "alice/X"), "old", ex=100)
    install_page(monkeypatch, ok_page())
    tt.scrape_threads_post("alice", "X")
    assert use_conn.keys("scrapling:dead:*") == []


def test_20_flag_off_writes_nothing(monkeypatch, use_conn):
    monkeypatch.setattr(settings, "skip_dead_targets", False)
    install_page(monkeypatch, FakePage(url="https://www.threads.com/?error=invalid_post"))
    for _ in range(3):
        with pytest.raises(ValueError):
            tt.scrape_threads_post("alice", "X")
    assert use_conn.keys("scrapling:dead:*") == []


def test_21_redis_down_never_masks_the_job_error(monkeypatch):
    monkeypatch.setattr(dead_targets, "_conn", lambda: redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2))
    install_page(monkeypatch, FakePage(url="https://www.threads.com/?error=invalid_post"))
    with pytest.raises(tt.ThreadsPostUnavailable):
        tt.scrape_threads_post("alice", "X")


def test_22_outside_rq_job_is_noop(monkeypatch):
    assert dead_targets._conn() is None
    assert dead_targets.record_permanent_failure("threads", "post", "a/1", "unavailable", "x") is False
    dead_targets.clear("threads", "post", "a/1")  # must not raise


def test_23_unknown_kind_does_not_raise(use_conn):
    assert dead_targets.record_permanent_failure("threads", "post", "a/1", "bogus", "x") is False


def test_24_threshold_zero_or_negative_treated_as_one(monkeypatch, use_conn):
    monkeypatch.setattr(settings, "dead_unavailable_threshold", 0)
    assert dead_targets.record_permanent_failure("threads", "post", "a/1", "unavailable", "x") is True


def test_25_concurrent_strikes_are_atomic(use_conn):
    """Two replicas failing the same post at once: INCR keeps the count exact."""
    import threading
    def hit():
        for _ in range(50):
            dead_targets.record_permanent_failure("threads", "post", "a/1", "parse", "x", conn=redis.Redis.from_url(R_URL))
    ts = [threading.Thread(target=hit) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert int(use_conn.get(dead_targets.strike_key("threads", "post", "a/1") + ":parse")) == 200


def test_26_long_reason_is_truncated(use_conn):
    dead_targets.record_permanent_failure("threads", "post", "a/1", "unavailable", "x" * 5000)
    dead_targets.record_permanent_failure("threads", "post", "a/1", "unavailable", "x" * 5000)
    assert len(dead(use_conn, "a/1")) <= 500


def test_27_key_contract_matches_api():
    import importlib.util
    spec = importlib.util.spec_from_file_location("api_dedup", "/app_src/services/dedup.py")
    api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
    for args in [("threads", "post", "u/C0de-_x"), ("instagram", "posts_detail", "user.name")]:
        assert api.dead_key(*args) == dead_targets.dead_key(*args)


# ------------------------------------------------------ 4. logging & misc ---
def test_28_forked_jobs_create_no_log_files(tmp_path):
    """Regression for the 132k-file bloat: fork 30 'jobs' that log; no files appear."""
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, '/worker'); os.chdir({str(tmp_path)!r})
        import worker
        from loguru import logger
        for i in range(30):
            pid = os.fork()
            if pid == 0:
                logger.info(f"job {{i}}"); os._exit(0)
            os.waitpid(pid, 0)
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert list(tmp_path.rglob("*")) == []
    lines = [l for l in out.stdout.splitlines() if " job " in l]
    assert len(lines) == 30, "each line must be logged exactly once (no duplicate stderr handler)"


def test_29_old_logging_setup_reproduces_bloat(tmp_path):
    """Proves the root cause: the old sink rotates once per forked job when started yesterday."""
    # Simulate "worker started yesterday" by creating the file with an old ctime-equivalent:
    # loguru computes the next 00:00 rotation from the file creation time at add().
    script = textwrap.dedent(f"""
        import os, time, datetime
        from loguru import logger
        p = os.path.join({str(tmp_path)!r}, 'worker.log')
        open(p, 'w').close()
        old = time.time() - 2 * 86400
        os.utime(p, (old, old))
        import loguru._file_sink as fs
        fs.get_ctime = lambda path: old  # creation time = 2 days ago
        logger.remove()
        logger.add(p, rotation='00:00', retention='30 days', serialize=True)
        for i in range(10):
            pid = os.fork()
            if pid == 0:
                logger.info('job'); os._exit(0)
            os.waitpid(pid, 0)
    """)
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)
    assert len(list(tmp_path.iterdir())) >= 10


def test_30_worker_names_unique_and_carry_hostname():
    import socket
    import worker
    names = {worker.worker_name() for _ in range(1000)}
    assert len(names) == 1000 and all(n.startswith(socket.gethostname()) for n in names)


def test_31_defaults_and_env_overrides(monkeypatch):
    for k in ["THREADS_BROWSER_VIEWS", "SKIP_DEAD_TARGETS"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.threads_browser_views is False and s.skip_dead_targets is True
    monkeypatch.setenv("THREADS_BROWSER_VIEWS", "true")
    monkeypatch.setenv("SKIP_DEAD_TARGETS", "false")
    s = Settings(_env_file=None)
    assert s.threads_browser_views is True and s.skip_dead_targets is False


def test_32_pinned_versions_match_production():
    want = {l.split("==")[0].lower(): l.split("==")[1] for l in open("/worker/constraints.txt").read().split() if "==" in l}
    have = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout
    have = {l.split("==")[0].lower(): l.split("==")[1] for l in have.split() if "==" in l}
    drift = {k: (v, have.get(k)) for k, v in want.items() if have.get(k) != v}
    assert not drift, drift
