import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.account import Platform
from app.models.job import JobStatus, JobType


class JobCreate(BaseModel):
    platform: Platform
    job_type: JobType
    target: str
    queue: str = "default"


class JobResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    platform: Platform
    job_type: JobType
    target: str
    status: JobStatus
    rq_job_id: str | None
    queue_name: str
    result: dict | None
    error: str | None
    enqueued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
