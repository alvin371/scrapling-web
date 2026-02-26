import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.post import Post
from app.models.account import Platform


class PostRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, post_id: uuid.UUID) -> Post | None:
        result = await self.db.execute(select(Post).where(Post.id == post_id))
        return result.scalar_one_or_none()

    async def list_all(
        self,
        account_id: uuid.UUID | None = None,
        platform: Platform | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Post]:
        q = select(Post)
        if account_id:
            q = q.where(Post.account_id == account_id)
        if platform:
            q = q.where(Post.platform == platform)
        q = q.order_by(Post.scraped_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(q)
        return list(result.scalars().all())
