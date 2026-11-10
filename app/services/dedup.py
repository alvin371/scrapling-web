"""Redis keys shared with the worker, plus the atomic helpers for in-flight dedup.

Key layout MUST match worker/dead_targets.py (tests/test_contract.py asserts it).
"""
DEAD_PREFIX = "scrapling:dead"
INFLIGHT_PREFIX = "scrapling:inflight"
CLAIM_PREFIX = "claim:"

# RQ statuses meaning "this job will still produce a result".
ACTIVE_RQ_STATUSES = {"queued", "started", "deferred", "scheduled"}


def dead_key(platform: str, job_type: str, target: str) -> str:
    return f"{DEAD_PREFIX}:target:{platform}:{job_type}:{target}"


def inflight_key(platform: str, job_type: str, target: str, queue: str) -> str:
    return f"{INFLIGHT_PREFIX}:{queue}:{platform}:{job_type}:{target}"


_CAS_SET = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3]) return 1
end
return 0
"""
_CAS_DEL = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


def compare_and_set(conn, key: str, expected: str, value: str, ex: int) -> bool:
    """Atomically replace key's value only if it still equals ``expected``."""
    return bool(conn.eval(_CAS_SET, 1, key, expected, value, int(ex)))


def compare_and_delete(conn, key: str, expected: str) -> bool:
    return bool(conn.eval(_CAS_DEL, 1, key, expected))
