import uuid
from fastapi import APIRouter, Query
from app.dependencies import DbSession, RedisClient, get_rq_queue
from app.models.account import Platform
from app.models.job import JobType
from app.schemas.job import JobCreate
from app.schemas.post import PostResponse, PostScrapeRequest, PostScrapeJobResponse
from app.services.post_service import PostService
from app.services.job_service import JobService
from app.core.link_parser import parse_post_link

router = APIRouter()


@router.get("", response_model=list[PostResponse])
async def list_posts(
    db: DbSession,
    redis: RedisClient,
    account_id: uuid.UUID | None = Query(None),
    platform: Platform | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await PostService(db, redis).list_posts(
        account_id=account_id, platform=platform, limit=limit, offset=offset
    )


@router.post("/scrape", response_model=PostScrapeJobResponse, status_code=201)
async def scrape_post(data: PostScrapeRequest, db: DbSession, redis: RedisClient):
    platform, target = parse_post_link(data.link)
    queue = get_rq_queue("default")
    job = await JobService(db, redis).enqueue(
        JobCreate(platform=platform, job_type=JobType.post, target=target),
        queue,
    )
    return PostScrapeJobResponse(job_id=job.id, platform=platform, post_url=data.link)
