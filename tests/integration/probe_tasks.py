"""Deterministic task mounted into the worker for integration tests only."""
import os
import socket
import time

from rq import get_current_job


def probe(token: str, sleep: float = 0.0):
    conn = get_current_job().connection
    conn.incr(f"it:exec:{token}")
    conn.rpush("it:who", f"{token}|{socket.gethostname()}")
    if sleep:
        conn.set(f"it:started:{token}", "1")
        time.sleep(sleep)
    conn.set(f"it:done:{token}", "1")
    return {"token": token, "host": socket.gethostname(), "pid": os.getpid()}
