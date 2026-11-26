from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VERITY_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/verity"
    redis_url: str = "redis://localhost:6379/0"

    # Public URL of the web app; used in email links and the origin check.
    app_url: str = "http://localhost:3000"
    allowed_origins: list[str] = ["http://localhost:3000"]

    # 32 url-safe base64 bytes (Fernet key) used to encrypt secrets at rest.
    # The dev default is NOT secret; production must set VERITY_SECRET_KEY.
    secret_key: str = "ZGV2LW9ubHktc2VjcmV0LWtleS1jaGFuZ2UtbWUtMDA="

    session_cookie: str = "verity_session"
    session_ttl_hours: int = 24 * 14
    session_cookie_secure: bool = False

    verify_token_ttl_hours: int = 24
    reset_token_ttl_minutes: int = 60
    invite_ttl_days: int = 7

    # Email: "smtp" (Mailpit in dev), "console", or "memory" (tests).
    email_backend: str = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_tls: bool = False
    email_from: str = "Verity <no-reply@verity.local>"

    # Rate limits: "<count>/<seconds>"
    rl_login: str = "10/60"
    rl_register: str = "5/600"
    rl_email: str = "3/600"
    login_max_failures: int = 5
    login_lockout_seconds: int = 900

    # First system administrator, created by `python -m app.seed` when set.
    admin_email: str | None = None
    admin_password: str | None = None

    payment_provider: str = "mock"

    @property
    def is_dev(self) -> bool:
        return self.env in ("dev", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
