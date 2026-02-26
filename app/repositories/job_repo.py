import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.job import ScrapeJob, JobStatus
from app.models.account import Platform
from app.schemas.job import JobCreate


class JobRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: JobCreate) -> ScrapeJob:
        job = ScrapeJob(
            platform=data.platform,
            job_type=data.job_type,
            target=data.target,
            queue_name=data.queue,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> ScrapeJob | None:
        result = await self.db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))
        return result.scalar_one_or_none()

    async def list_all(
        self,
        status: JobStatus | None = None,
        platform: Platform | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScrapeJob]:
        q = select(ScrapeJob)
        if status:
            q = q.where(ScrapeJob.status == status)
        if platform:
            q = q.where(ScrapeJob.platform == platform)
        q = q.order_by(ScrapeJob.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def update_rq_job_id(self, job: ScrapeJob, rq_job_id: str) -> ScrapeJob:
        job.rq_job_id = rq_job_id
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def update_status(self, job: ScrapeJob, status: JobStatus, **kwargs) -> ScrapeJob:
        job.status = status
        for field, value in kwargs.items():
            setattr(job, field, value)
        await self.db.commit()
        await self.db.refresh(job)
        return job
