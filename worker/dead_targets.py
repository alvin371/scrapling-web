"""Dead-target bookkeeping shared (by key format) with the API.

A *permanent* scrape failure adds a strike of its kind for ``{platform}:{job_type}:{target}``:
  - "unavailable": Threads redirected to ?error=invalid_post (deleted/private/invalid post)
  - "parse":       the page loaded but carried no post data
Once a kind reaches its threshold the target is marked dead for that kind's TTL (config.py);
the API then fails new submissions for it instantly (see app/services/dedup.py).
A successful scrape clears both keys, so a post that comes back is retried normally.

Transient failures (network, timeouts, real rate limits) never add strikes.
The key layout MUST stay identical to app/services/dedup.py (a test asserts it).
"""
from loguru import logger

KEY_PREFIX = "scrapling:dead"


def strike_key(platform: str, job_type: str, target: str) -> str:
    return f"{KEY_PREFIX}:strikes:{platform}:{job_type}:{target}"


def dead_key(platform: str, job_type: str, target: str) -> str:
    return f"{KEY_PREFIX}:target:{platform}:{job_type}:{target}"


def _conn():
    """Redis connection of the job being executed (None outside an RQ job)."""
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.connection if job else None
    except Exception:  # noqa: BLE001
        return None


def _policy(kind: str, settings) -> tuple[int, int]:
    if kind == "unavailable":
        return settings.dead_unavailable_threshold, settings.dead_unavailable_ttl_seconds
    if kind == "parse":
        return settings.dead_parse_threshold, settings.dead_parse_ttl_seconds
    raise ValueError(f"unknown failure kind: {kind}")


def record_permanent_failure(platform: str, job_type: str, target: str, kind: str, reason: str, conn=None) -> bool:
    """Add a strike of ``kind`` ("unavailable" | "parse"); mark the target dead when that
    kind's threshold is reached. Returns True if the target is now dead.
    Never raises: bookkeeping must not change the job's own outcome."""
    from config import settings
    if not settings.skip_dead_targets:
        return False
    try:
        threshold, ttl = _policy(kind, settings)
        conn = conn or _conn()
        if conn is None:
            return False
        sk = f"{strike_key(platform, job_type, target)}:{kind}"
        pipe = conn.pipeline()
        pipe.incr(sk)
        pipe.expire(sk, settings.dead_strike_window_seconds)
        strikes = int(pipe.execute()[0])
        if strikes >= max(1, threshold):
            conn.set(dead_key(platform, job_type, target), f"{kind}: {reason}"[:500], ex=max(1, ttl))
            logger.warning(f"[dead-target] {platform}:{job_type}:{target} dead for {ttl}s after {strikes} {kind} strikes")
            return True
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[dead-target] bookkeeping failed for {target}: {exc}")
        return False


def clear(platform: str, job_type: str, target: str, conn=None) -> None:
    """Forget strikes/dead mark after a successful scrape. Never raises."""
    conn = conn or _conn()
    if conn is None:
        return
    try:
        sk = strike_key(platform, job_type, target)
        conn.delete(f"{sk}:unavailable", f"{sk}:parse", dead_key(platform, job_type, target))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[dead-target] clear failed for {target}: {exc}")
