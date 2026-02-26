import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, Enum as SAEnum, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.models.account import Platform


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobType(str, enum.Enum):
    profile = "profile"
    posts = "posts"
    followers = "followers"
    following = "following"
    posts_detail = "posts_detail"
    post = "post"


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform, name="platform_enum"), nullable=False)
    job_type: Mapped[JobType] = mapped_column(SAEnum(JobType, name="job_type_enum"), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus, name="job_status_enum"), default=JobStatus.pending)
    rq_job_id: Mapped[str | None] = mapped_column(String(255))
    queue_name: Mapped[str] = mapped_column(String(50), default="default")
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    enqueued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
