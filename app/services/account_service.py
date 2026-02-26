import uuid
import json
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.repositories.account_repo import AccountRepo
from app.models.account import Platform
from app.schemas.account import AccountCreate, AccountUpdate, AccountResponse
from app.core.redis_client import CACHE_TTL


class AccountService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = AccountRepo(db)
        self.redis = redis

    async def get_or_create(self, data: AccountCreate) -> AccountResponse:
        existing = await self.repo.get_by_platform_username(data.platform, data.username)
        if existing:
            return AccountResponse.model_validate(existing)
        account = await self.repo.create(data)
        return AccountResponse.model_validate(account)

    async def get(self, account_id: uuid.UUID) -> AccountResponse:
        cache_key = f"account:{account_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            return AccountResponse.model_validate_json(cached)

        account = await self.repo.get_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        response = AccountResponse.model_validate(account)
        await self.redis.setex(cache_key, CACHE_TTL["account"], response.model_dump_json())
        return response

    async def list_accounts(self, platform: Platform | None = None, limit: int = 50, offset: int = 0) -> list[AccountResponse]:
        accounts = await self.repo.list_all(platform=platform, limit=limit, offset=offset)
        return [AccountResponse.model_validate(a) for a in accounts]

    async def update(self, account_id: uuid.UUID, data: AccountUpdate) -> AccountResponse:
        account = await self.repo.get_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        account = await self.repo.update(account, data)
        await self.redis.delete(f"account:{account_id}")
        return AccountResponse.model_validate(account)

    async def delete(self, account_id: uuid.UUID) -> None:
        account = await self.repo.get_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        await self.repo.delete(account)
        await self.redis.delete(f"account:{account_id}")
