from typing import Annotated
from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from redis import Redis as SyncRedis
from rq import Queue
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.config import settings

DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def get_rq_queue(name: str = "default") -> Queue:
    conn = SyncRedis.from_url(settings.redis_url)
    return Queue(name, connection=conn)
