from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = "change-me"
    log_level: str = "INFO"
    log_path: str = "logs/app.log"
    api_keys: str = ""  # comma-separated, e.g. "key1,key2"

    # CORS allowed origins: comma-separated list, or "*" for any (dev default).
    # In production set e.g. CORS_ORIGINS=https://scrap.acnenosystem.com
    cors_origins: str = "*"

    @property
    def api_keys_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cors_origins_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins or ["*"]

    db_host: str = "localhost"
    db_port: int = 5432
    postgres_user: str = "scraper"
    postgres_password: str = "scraper_pass"
    postgres_db: str = "social_scraper"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.db_host}:{self.db_port}/{self.postgres_db}"
        )

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    scrapling_proxy: str = ""

    # --- RQ job lifetimes (seconds) --------------------------------------------
    rq_job_timeout: int = 300
    # How long a finished job's result stays in Redis for GET /jobs/{id} to pick up
    # (RQ default 500 s is shorter than the clients' poll + backlog window). 1 h was too short:
    # on 2026-11-11 bka-revamp polled Threads jobs ~65 min after submitting (behind a 9k TikTok
    # backlog) and found them expired. A poll copies the result into Postgres for good.
    rq_result_ttl: int = 6 * 3600
    # How long failed jobs stay in Redis. RQ's default is 1 year, which had piled up
    # ~26k failed jobs (~2/3 of Redis memory) by 2026-11-10.
    rq_failure_ttl: int = 3 * 86400

    # --- Feature flags -----------------------------------------------------------
    # Fail submissions for targets the worker marked dead (worker/dead_targets.py)
    # instantly, without enqueueing. Set false to ignore dead marks immediately.
    skip_dead_targets: bool = True
    # Reuse the job already queued/running for the same platform+type+target+queue
    # instead of enqueueing a duplicate (clients resubmit when their poll times out).
    dedup_inflight_jobs: bool = True
    inflight_ttl_seconds: int = 3600   # upper bound on how long a job is considered in flight
    dedup_claim_ttl_seconds: int = 15     # a claim auto-expires if its request dies mid-enqueue
    dedup_claim_wait_seconds: float = 10.0  # waiters give up on a slow claim and fail open


settings = Settings()
