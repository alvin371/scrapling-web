from fastapi import APIRouter, Depends
from app.api.v1 import health, accounts, jobs, posts
from app.core.rate_limit import check_rate_limit

api_router = APIRouter()

# Public — no auth (Docker health checks, uptime monitors)
api_router.include_router(health.router, tags=["health"])

# Protected — auth + rate limit (600 req/min per API key)
_auth = [Depends(check_rate_limit)]
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"], dependencies=_auth)
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"], dependencies=_auth)
api_router.include_router(posts.router, prefix="/posts", tags=["posts"], dependencies=_auth)
