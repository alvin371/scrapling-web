import uuid
from fastapi import APIRouter, Query
from app.dependencies import DbSession, RedisClient, get_rq_queue
from app.models.account import Platform
from app.models.job import JobType
from app.schemas.account import AccountCreate, AccountUpdate, AccountResponse, AccountCreateFromLink, AccountWithJobResponse
from app.schemas.job import JobCreate
from app.services.account_service import AccountService
from app.services.job_service import JobService
from app.core.link_parser import parse_link

router = APIRouter()


@router.post("", response_model=AccountWithJobResponse, status_code=201)
async def create_account(data: AccountCreateFromLink, db: DbSession, redis: RedisClient):
    platform, username = parse_link(data.link)
    account = await AccountService(db, redis).get_or_create(AccountCreate(platform=platform, username=username))
    queue = get_rq_queue("default")
    job = await JobService(db, redis).enqueue(
        JobCreate(platform=platform, job_type=JobType.posts_detail, target=username),
        queue,
    )
    return AccountWithJobResponse(account=account, job_id=job.id)


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    db: DbSession,
    redis: RedisClient,
    platform: Platform | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await AccountService(db, redis).list_accounts(platform=platform, limit=limit, offset=offset)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: uuid.UUID, db: DbSession, redis: RedisClient):
    return await AccountService(db, redis).get(account_id)


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(account_id: uuid.UUID, data: AccountUpdate, db: DbSession, redis: RedisClient):
    return await AccountService(db, redis).update(account_id, data)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: uuid.UUID, db: DbSession, redis: RedisClient):
    await AccountService(db, redis).delete(account_id)
