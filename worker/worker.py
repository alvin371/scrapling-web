import os
import socket
import sys
import uuid

from redis import Redis
from rq import Worker, Queue
from config import settings
from loguru import logger

# Log to stdout only (Docker's json-file driver rotates it; see docker-stack.yml).
#
# The previous file sink (rotation="00:00", retention="30 days") broke under RQ's
# fork-per-job model: every forked work-horse inherited a sink whose next rotation
# time was already in the past, so each job rotated the file once and then ran the
# 30-day retention glob. Result (2026-11-10): ~5,500 new files/day, 132k files in
# logs/, ~900 MB of kernel dentry cache charged to the worker container and a glob
# over the whole directory on every job. logger.remove() also drops loguru's default
# stderr handler, which made every line appear twice in `docker logs`.
logger.remove()
logger.add(sys.stdout, level=settings.log_level)


def worker_name() -> str:
    """Unique per process, readable per replica (Swarm sets the hostname per task)."""
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


if __name__ == "__main__":
    redis_conn = Redis.from_url(settings.redis_url)
    queues = [Queue(name, connection=redis_conn) for name in ["high", "default", "low"]]
    name = worker_name()
    logger.info(f"RQ Worker {name} starting — queues: {[q.name for q in queues]}, "
                f"threads_browser_views={settings.threads_browser_views}, "
                f"skip_dead_targets={settings.skip_dead_targets}")
    Worker(queues, connection=redis_conn, name=name).work()
