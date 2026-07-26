from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from app.core.logging import setup_logging
from app.core.database import engine
from app.api.v1.router import api_router
from app.config import settings

logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting social-scraper-service [{settings.app_env}]")
    if not settings.api_keys_set:
        logger.warning("No API keys configured — all protected endpoints will reject requests.")
    yield
    logger.info("Shutting down social-scraper-service")
    await engine.dispose()


app = FastAPI(title="Social Scraper Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["ApiKeyAuth"] = {
        "type": "apiKey", "in": "header", "name": "X-API-Key"
    }
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
