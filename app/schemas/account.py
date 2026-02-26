import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.account import Platform


class AccountCreateFromLink(BaseModel):
    link: str  # e.g. "https://www.instagram.com/cerita.joni"


class AccountCreate(BaseModel):
    platform: Platform
    username: str
    display_name: str | None = None
    bio: str | None = None


class AccountUpdate(BaseModel):
    display_name: str | None = None
    bio: str | None = None
    followers: int | None = None
    following: int | None = None
    post_count: int | None = None
    is_private: bool | None = None
    raw_data: dict | None = None


class AccountResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    platform: Platform
    username: str
    display_name: str | None
    bio: str | None
    followers: int
    following: int
    post_count: int
    is_private: bool
    scraped_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AccountWithJobResponse(BaseModel):
    account: AccountResponse
    job_id: uuid.UUID
