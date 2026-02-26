from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = "change-me"
    log_level: str = "INFO"
    log_path: str = "logs/app.log"
    api_keys: str = ""  # comma-separated, e.g. "key1,key2"

    @property
    def api_keys_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

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


settings = Settings()
