import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, Text, Enum as SAEnum, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
import enum


class Platform(str, enum.Enum):
    instagram = "instagram"
    threads = "threads"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform, name="platform_enum"), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    bio: Mapped[str | None] = mapped_column(Text)
    followers: Mapped[int] = mapped_column(Integer, default=0)
    following: Mapped[int] = mapped_column(Integer, default=0)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    scraped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
