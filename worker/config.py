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


settings = Settings()
