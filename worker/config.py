from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    log_path: str = "logs/worker.log"

    db_host: str = "localhost"
    db_port: int = 5432
    postgres_user: str = "scraper"
    postgres_password: str = "scraper_pass"
    postgres_db: str = "social_scraper"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.db_host}:{self.db_port}/{self.postgres_db}"
        )

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    scrapling_proxy: str = ""
    instagram_session_id: str = ""  # paste sessionid cookie value from a logged-in browser
    threads_session_id: str = ""  # paste sessionid cookie value from a logged-in Threads browser session

    # --- Feature flags (env: upper-case name) ---------------------------------
    # Open a headless Chromium per Threads post to read the exact view count.
    # Off by default: measured 2026-11-10, 0 of 27,325 posts got a count this way,
    # while the plain HTTP response already carries the (approximate) view count.
    threads_browser_views: bool = False
    # Remember targets that keep failing permanently (deleted/invalid post, no data)
    # so the API can fail repeat submissions instantly instead of re-scraping them.
    skip_dead_targets: bool = True
    # "unavailable" = Threads redirected to ?error=invalid_post (deleted/private post).
    dead_unavailable_threshold: int = 2
    dead_unavailable_ttl_seconds: int = 7 * 86400
    # "parse" = page loaded without post data. Could also be a Threads layout change
    # hitting every post, so it needs more strikes and is skipped for a shorter time.
    dead_parse_threshold: int = 3
    dead_parse_ttl_seconds: int = 86400
    dead_strike_window_seconds: int = 3 * 86400  # strikes older than this are forgotten


settings = Settings()
