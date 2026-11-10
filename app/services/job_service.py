import asyncio
import time
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
from app.config import settings
from app.services import dedup
from loguru import logger


class _RedisUnavailable(Exception):
    pass


class JobService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = JobRepo(db)
        self.redis = redis

    async def enqueue(self, data: JobCreate, queue: Queue) -> JobResponse:
        # Validate the platform/job_type/target before touching DB or Redis.
        task_fn, kwargs = self._resolve_task(data)
        conn = queue.connection
        platform, job_type = data.platform.value, data.job_type.value

        if settings.skip_dead_targets:
            reason = self._redis_get(conn, dedup.dead_key(platform, job_type, data.target))
            if reason is not None:
                job_record = await self.repo.create_failed(
                    data, f"Skipped: target previously failed permanently ({reason})"
                )
                logger.info(f"[enqueue] skipped dead target {platform}:{job_type}:{data.target}")
                return JobResponse.model_validate(job_record)

        if not settings.dedup_inflight_jobs:
            return await self._create_and_enqueue(data, queue, task_fn, kwargs)

        ikey = dedup.inflight_key(platform, job_type, data.target, data.queue)
        return await self._dedup_enqueue(conn, ikey, data, queue, task_fn, kwargs)

    async def _dedup_enqueue(self, conn, ikey: str, data: JobCreate, queue: Queue, task_fn: str, kwargs: dict) -> JobResponse:
        """At most one in-flight job per key, without serialising requests behind a lock.

        The key holds either ``claim:<token>`` (a request is creating the job right now)
        or the scrape_jobs id of the in-flight job. Exactly one request wins the claim
        (SET NX, or compare-and-set over a stale value); everyone else only reads and
        waits for the winner's job id. Redis trouble, or a claim older than
        dedup_claim_wait_seconds, fails open: the request enqueues on its own."""
        claim = f"{dedup.CLAIM_PREFIX}{uuid.uuid4().hex}"
        deadline = time.monotonic() + settings.dedup_claim_wait_seconds
        try:
            while True:
                raw = self._redis_get(conn, ikey, raise_errors=True)
                if raw is None:
                    if self._call(conn.set, ikey, claim, nx=True, ex=settings.dedup_claim_ttl_seconds):
                        return await self._create_claimed(conn, ikey, claim, data, queue, task_fn, kwargs)
                    continue
                if raw.startswith(dedup.CLAIM_PREFIX):
                    if time.monotonic() >= deadline:
                        logger.warning(f"[enqueue] {ikey} claimed for too long, enqueueing without dedup")
                        return await self._create_and_enqueue(data, queue, task_fn, kwargs)
                    await asyncio.sleep(0.05)
                    continue
                existing = await self._active_job(conn, raw)
                if existing is not None:
                    logger.info(f"[enqueue] reusing in-flight job {existing.id} for {ikey}")
                    return JobResponse.model_validate(existing)
                if self._call(dedup.compare_and_set, conn, ikey, raw, claim, settings.dedup_claim_ttl_seconds):
                    return await self._create_claimed(conn, ikey, claim, data, queue, task_fn, kwargs)
                # Someone else replaced the stale value first: re-read.
        except _RedisUnavailable as exc:
            logger.warning(f"[enqueue] dedup unavailable, enqueueing without dedup: {exc}")
            return await self._create_and_enqueue(data, queue, task_fn, kwargs)

    async def _create_claimed(self, conn, ikey, claim, data, queue, task_fn, kwargs) -> JobResponse:
        try:
            response = await self._create_and_enqueue(data, queue, task_fn, kwargs)
        except BaseException:
            try:
                dedup.compare_and_delete(conn, ikey, claim)  # let the next request retry
            except Exception:  # noqa: BLE001
                pass
            raise
        try:
            if not dedup.compare_and_set(conn, ikey, claim, str(response.id), settings.inflight_ttl_seconds):
                logger.warning(f"[enqueue] claim on {ikey} expired before the job was recorded")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[enqueue] could not record in-flight job: {exc}")
        return response

    async def _create_and_enqueue(self, data: JobCreate, queue: Queue, task_fn: str, kwargs: dict) -> JobResponse:
        job_record = await self.repo.create(data)
        rq_job = queue.enqueue(
            task_fn,
            **kwargs,
            job_timeout=settings.rq_job_timeout,
            result_ttl=settings.rq_result_ttl,
            failure_ttl=settings.rq_failure_ttl,
        )
        await self.repo.update_rq_job_id(job_record, rq_job.id)
        return JobResponse.model_validate(job_record)

    async def _active_job(self, conn, raw: str) -> ScrapeJob | None:
        """The scrape_jobs row ``raw`` if its RQ job is still queued/running, else None."""
        try:
            job_id = uuid.UUID(raw)
        except ValueError:
            return None
        job = await self.repo.get_by_id(job_id)
        if not job or not job.rq_job_id or job.status in (JobStatus.completed, JobStatus.failed):
            return None
        rq_job = self._fetch_rq_job(job.rq_job_id, conn)
        if rq_job is None:
            return None
        try:
            status = rq_job.get_status()
        except Exception:  # noqa: BLE001
            return None
        status = getattr(status, "value", status)
        return job if status in dedup.ACTIVE_RQ_STATUSES else None

    @staticmethod
    def _call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _RedisUnavailable(str(exc)) from exc

    @staticmethod
    def _redis_get(conn, key: str, raise_errors: bool = False) -> str | None:
        try:
            raw = conn.get(key)
        except Exception as exc:  # noqa: BLE001 — fail open
            if raise_errors:
                raise _RedisUnavailable(str(exc)) from exc
            logger.warning(f"[enqueue] redis get {key} failed: {exc}")
            return None
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

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
                username, _, post_code = data.target.partition("/")
                if not username or not post_code:
                    raise HTTPException(status_code=400, detail="Threads post target must be 'username/post_code'")
                return "tasks.threads_tasks.scrape_threads_post", {"username": username, "post_code": post_code}
        raise HTTPException(status_code=400, detail="Unsupported platform/job_type combination")

    def _fetch_rq_job(self, rq_job_id: str, conn=None):
        """Fetch the RQ job object from Redis (sync). Returns None if not found/expired."""
        try:
            from rq.job import Job as RQJob
            if conn is None:
                from redis import Redis as SyncRedis
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
