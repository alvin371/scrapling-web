import time
from fastapi import Depends, HTTPException
from redis.asyncio import Redis
from app.core.auth import verify_api_key
from app.core.redis_client import get_redis

RATE_LIMIT = 600   # requests
WINDOW_SEC = 60    # per minute


async def check_rate_limit(
    api_key: str = Depends(verify_api_key),
    redis: Redis = Depends(get_redis),
) -> str:
    window = int(time.time() // WINDOW_SEC)
    key = f"rl:{api_key}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, WINDOW_SEC + 1)
    if count > RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT} requests per minute",
            headers={"Retry-After": str(WINDOW_SEC)},
        )
    return api_key
