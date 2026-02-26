import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, ForeignKey, Enum as SAEnum, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.models.account import Platform


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"))
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform, name="platform_enum"), nullable=False)
    post_id: Mapped[str] = mapped_column(String(255), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(50))
    media_url: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(Text)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    posted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
