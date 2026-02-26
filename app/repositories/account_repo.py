import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.account import Account, Platform
from app.schemas.account import AccountCreate, AccountUpdate


class AccountRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: AccountCreate) -> Account:
        account = Account(**data.model_dump())
        self.db.add(account)
        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def get_by_id(self, account_id: uuid.UUID) -> Account | None:
        result = await self.db.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()

    async def get_by_platform_username(self, platform: Platform, username: str) -> Account | None:
        result = await self.db.execute(
            select(Account).where(Account.platform == platform, Account.username == username)
        )
        return result.scalar_one_or_none()

    async def list_all(self, platform: Platform | None = None, limit: int = 50, offset: int = 0) -> list[Account]:
        q = select(Account)
        if platform:
            q = q.where(Account.platform == platform)
        q = q.limit(limit).offset(offset)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def update(self, account: Account, data: AccountUpdate) -> Account:
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(account, field, value)
        await self.db.commit()
        await self.db.refresh(account)
        return account

    async def delete(self, account: Account) -> None:
        await self.db.delete(account)
        await self.db.commit()
