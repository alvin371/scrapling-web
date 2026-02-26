import uuid
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from rq import Queue
from app.repositories.job_repo import JobRepo
from app.models.job import JobStatus, ScrapeJob
from app.models.account import Platform
from app.schemas.job import JobCreate, JobResponse
from app.core.redis_client import CACHE_TTL


class JobService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = JobRepo(db)
        self.redis = redis

    async def enqueue(self, data: JobCreate, queue: Queue) -> JobResponse:
        job_record = await self.repo.create(data)

        task_fn, kwargs = self._resolve_task(data)
        rq_job = queue.enqueue(task_fn, **kwargs, job_timeout=300)

        await self.repo.update_rq_job_id(job_record, rq_job.id)
        return JobResponse.model_validate(job_record)

    def _resolve_task(self, data: JobCreate) -> tuple[str, dict]:
        """Return a dotted import path string so the API never imports worker deps.
        Paths are relative to the worker container's WORKDIR (/worker), so no 'worker.' prefix."""
        if data.platform.value == "instagram":
            if data.job_type.value == "profile":
                return "tasks.instagram_tasks.scrape_instagram_profile", {"username": data.target}
            elif data.job_type.value == "posts":
                return "tasks.instagram_tasks.scrape_instagram_posts", {"username": data.target}
            elif data.job_type.value == "posts_detail":
                return "tasks.instagram_tasks.scrape_instagram_posts_detailed", {"username": data.target, "limit": 5}
            elif data.job_type.value == "post":
                return "tasks.instagram_tasks.scrape_instagram_post", {"shortcode": data.target}
        elif data.platform.value == "threads":
            if data.job_type.value == "profile":
                return "tasks.threads_tasks.scrape_threads_profile", {"username": data.target}
            elif data.job_type.value in ("posts", "posts_detail"):
                return "tasks.threads_tasks.scrape_threads_posts", {"username": data.target}
            elif data.job_type.value == "post":
                username, post_code = data.target.split("/", 1)
                return "tasks.threads_tasks.scrape_threads_post", {"username": username, "post_code": post_code}
        raise HTTPException(status_code=400, detail="Unsupported platform/job_type combination")

    def _fetch_rq_job(self, rq_job_id: str):
        """Fetch the RQ job object from Redis (sync). Returns None if not found/expired."""
        try:
            from rq.job import Job as RQJob
            from redis import Redis as SyncRedis
            from app.config import settings
            conn = SyncRedis.from_url(settings.redis_url)
            return RQJob.fetch(rq_job_id, connection=conn)
        except Exception:
            return None

    async def _sync_from_rq(self, job: ScrapeJob) -> ScrapeJob:
        """Check RQ for job completion and persist the result to DB."""
        rq_job = self._fetch_rq_job(job.rq_job_id)
        if not rq_job:
            return job

        rq_status = rq_job.get_status()

        if rq_status.value == "finished":
            result = rq_job.result
            # JSONB column is dict|None — wrap lists so the type is always a dict
            if isinstance(result, list):
                result = {"data": result}
            return await self.repo.update_status(
                job,
                JobStatus.completed,
                result=result,
                started_at=rq_job.started_at,
                finished_at=rq_job.ended_at,
            )

        if rq_status.value == "failed":
            return await self.repo.update_status(
                job,
                JobStatus.failed,
                error=str(rq_job.exc_info) if rq_job.exc_info else "Job failed",
                started_at=rq_job.started_at,
                finished_at=rq_job.ended_at,
            )

        if rq_status.value == "started" and job.status == JobStatus.pending:
            return await self.repo.update_status(
                job,
                JobStatus.running,
                started_at=rq_job.started_at,
            )

        return job

    async def get(self, job_id: uuid.UUID) -> JobResponse:
        cache_key = f"job:{job_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            return JobResponse.model_validate_json(cached)

        job = await self.repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Sync status/result from RQ when DB hasn't been updated yet
        if job.status in (JobStatus.pending, JobStatus.running) and job.rq_job_id:
            job = await self._sync_from_rq(job)

        response = JobResponse.model_validate(job)
        if job.status in (JobStatus.completed, JobStatus.failed):
            await self.redis.setex(cache_key, CACHE_TTL["job_status"], response.model_dump_json())
        return response

    async def list_jobs(
        self,
        status: JobStatus | None = None,
        platform: Platform | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobResponse]:
        jobs = await self.repo.list_all(status=status, platform=platform, limit=limit, offset=offset)
        return [JobResponse.model_validate(j) for j in jobs]
