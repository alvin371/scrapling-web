import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.repositories.post_repo import PostRepo
from app.models.account import Platform
from app.schemas.post import PostResponse
from app.core.redis_client import CACHE_TTL


class PostService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = PostRepo(db)
        self.redis = redis

    async def list_posts(
        self,
        account_id: uuid.UUID | None = None,
        platform: Platform | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PostResponse]:
        cache_key = f"posts:{account_id}:{platform}:{limit}:{offset}"
        cached = await self.redis.get(cache_key)
        if cached:
            import json
            data = json.loads(cached)
            return [PostResponse.model_validate(p) for p in data]

        posts = await self.repo.list_all(account_id=account_id, platform=platform, limit=limit, offset=offset)
        result = [PostResponse.model_validate(p) for p in posts]

        import json
        await self.redis.setex(cache_key, CACHE_TTL["posts"], json.dumps([p.model_dump(mode="json") for p in result]))
        return result
