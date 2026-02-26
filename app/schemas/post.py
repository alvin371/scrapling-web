import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.account import Platform


class PostScrapeRequest(BaseModel):
    link: str


class PostScrapeJobResponse(BaseModel):
    job_id: uuid.UUID
    platform: Platform
    post_url: str


class PostResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    account_id: uuid.UUID | None
    platform: Platform
    post_id: str
    caption: str | None
    media_type: str | None
    media_url: str | None
    permalink: str | None
    likes: int
    comments: int
    shares: int
    views: int
    posted_at: datetime | None
    scraped_at: datetime
    created_at: datetime
