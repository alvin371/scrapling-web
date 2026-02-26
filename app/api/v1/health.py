from fastapi import APIRouter
from sqlalchemy import text
from app.dependencies import DbSession, RedisClient

router = APIRouter()


@router.get("/health")
async def health_check(db: DbSession, redis: RedisClient):
    db_status = "disconnected"
    redis_status = "disconnected"

    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        pass

    try:
        await redis.ping()
        redis_status = "connected"
    except Exception:
        pass

    return {"status": "ok", "db": db_status, "redis": redis_status}
