import redis.asyncio as aioredis
from app.config import settings

redis_pool = aioredis.ConnectionPool.from_url(settings.redis_url, max_connections=20)


async def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=redis_pool)


# TTL constants (seconds)
CACHE_TTL = {
    "account":    3600,   # 1 hour   — profile data
    "posts":       900,   # 15 min   — post lists
    "job_status": 86400,  # 24 hrs   — job result
    "rate_limit":    60,  # 1 min    — rate limit counters
}
