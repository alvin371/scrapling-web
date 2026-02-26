import uuid
from fastapi import APIRouter, Query, Depends
from rq import Queue
from app.dependencies import DbSession, RedisClient, get_rq_queue
from app.models.job import JobStatus
from app.models.account import Platform
from app.schemas.job import JobCreate, JobResponse
from app.services.job_service import JobService

router = APIRouter()


@router.post("", response_model=JobResponse, status_code=201)
async def create_job(
    data: JobCreate,
    db: DbSession,
    redis: RedisClient,
):
    queue = get_rq_queue(data.queue)
    return await JobService(db, redis).enqueue(data, queue)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    db: DbSession,
    redis: RedisClient,
    status: JobStatus | None = Query(None),
    platform: Platform | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await JobService(db, redis).list_jobs(status=status, platform=platform, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: DbSession, redis: RedisClient):
    return await JobService(db, redis).get(job_id)
