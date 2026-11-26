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

    # Background jobs: "saq" (Redis queue, run `python -m app.worker`) or "inline" (tests: run immediately).
    queue: str = "saq"

    # Uploaded files and chatbot logos. Local disk for now, behind app.services.storage.Storage.
    storage_dir: str = "./storage"
    max_upload_mb: int = 25

    # AI providers: "anthropic" / "voyage" for real calls, "fake" for tests, CI and e2e (deterministic, no network).
    llm_provider: str = "anthropic"
    embedding_provider: str = "voyage"
    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None
    voyage_url: str = "https://api.voyageai.com/v1/embeddings"
    embedding_model: str = "voyage-4"
    default_chat_model: str = "claude-opus-5-5"
    chat_models: list[str] = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    llm_timeout_seconds: float = 60.0

    # Website crawling
    crawl_user_agent: str = "VerityBot/1.0 (+https://verity.local/bot)"
    crawl_max_pages_per_run: int = 500
    crawl_max_page_bytes: int = 5 * 1024 * 1024
    crawl_timeout_seconds: float = 15.0
    # Tests and local development may crawl a server on localhost. Never enable in production.
    crawl_allow_private: bool = False

    # Public chatbot widget rate limits
    rl_widget_message: str = "20/60"  # per visitor
    rl_widget_ip: str = "60/60"  # per IP
    rl_widget_bot: str = "600/60"  # per chatbot

    @property
    def is_dev(self) -> bool:
        return self.env in ("dev", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
