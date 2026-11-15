from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # gateway
    gateway_host: str = Field(default="0.0.0.0", alias="CAPTCHA_GATEWAY_HOST")
    gateway_port: int = Field(default=8080, alias="CAPTCHA_GATEWAY_PORT")
    gateway_workers: int = Field(default=4, alias="CAPTCHA_GATEWAY_WORKERS")
    # Contract §2/§7: the sellable set is re-read from live heartbeats on this
    # interval instead of once at boot. Worker beats are 30 s, TTL 90 s, so a
    # dead lane stops being sold within TTL + one refresh.
    gateway_types_refresh_sec: int = Field(default=20, ge=5, le=300, alias="CAPTCHA_TYPES_REFRESH_SEC")
    # Manual declarations unioned into the sellable set (contract §7), comma or
    # semicolon separated. Unknown names are ignored; heartbeat still owns the
    # default path, this only covers a type the registry cannot see.
    captcha_extra_types: str = Field(default="", alias="CAPTCHA_EXTRA_TYPES")
    # Contract §7 freezes ALLOW_EXPERIMENTAL_TASKS=0 for production. The current
    # beta ships it as True so the 41 experimental solvers stay sellable; this
    # is a deliberate, temporary divergence — set it to 0 to restore §7.
    allow_experimental_tasks: bool = Field(default=True, alias="ALLOW_EXPERIMENTAL_TASKS")

    # redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_stream_prefix: str = Field(default="captcha", alias="REDIS_STREAM_PREFIX")

    # postgres
    postgres_url: str = Field(
        default="postgresql+asyncpg://captcha:captcha@localhost:5432/captcha",
        alias="POSTGRES_URL",
    )
    postgres_pool_size: int = Field(default=20, alias="POSTGRES_POOL_SIZE")

    # s3
    s3_endpoint: str = Field(default="http://localhost:9000", alias="S3_ENDPOINT")
    s3_access_key: str = Field(default="minioadmin", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="minioadmin", alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="captcha", alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")

    # worker
    worker_http_concurrency: int = Field(default=512, alias="WORKER_HTTP_CONCURRENCY")
    worker_http_impersonate: str = Field(default="chrome124", alias="WORKER_HTTP_IMPERSONATE")
    worker_cv_device: str = Field(default="cpu", alias="WORKER_CV_DEVICE")
    worker_browser_sessions: int = Field(default=12, alias="WORKER_BROWSER_SESSIONS")
    worker_browser_headless: bool = Field(default=True, alias="WORKER_BROWSER_HEADLESS")
    worker_browser_min_free_mb: int = Field(default=2048, alias="WORKER_BROWSER_MIN_FREE_MB")
    # per-task hard deadline, contract §4 window is 180–300 s
    worker_browser_task_timeout_sec: int = Field(default=240, alias="WORKER_BROWSER_TASK_TIMEOUT_SEC")
    # sessions the worker starts with; it ramps up to worker_browser_sessions
    worker_browser_ramp_start: int = Field(default=2, alias="WORKER_BROWSER_RAMP_START")

    # canary
    canary_interval_sec: int = Field(default=21600, alias="CANARY_INTERVAL_SEC")
    canary_alert_webhook: str = Field(default="", alias="CANARY_ALERT_WEBHOOK")

    # HTTP-first router
    http_fallback_threshold: float = Field(default=0.7, alias="HTTP_FALLBACK_THRESHOLD")
    http_fallback_window_sec: int = Field(default=3600, alias="HTTP_FALLBACK_WINDOW_SEC")

    # proxy
    proxy_provider_primary: str = Field(default="", alias="PROXY_PROVIDER_PRIMARY")
    proxy_provider_fallback: str = Field(default="", alias="PROXY_PROVIDER_FALLBACK")
    proxy_pool_file: str = Field(default="data/proxies/pool.txt", alias="PROXY_POOL_FILE")

    # llm (text captcha)
    llm_endpoint: str = Field(default="http://localhost:11434", alias="LLM_ENDPOINT")
    llm_model: str = Field(default="llama3.1:8b", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

    # observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")
    otel_endpoint: str = Field(default="", alias="OTEL_ENDPOINT")
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")

    # admin surface: empty token disables /admin/* entirely
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    # security: api-key hashing. Empty pepper still hashes, but loses the
    # rainbow-table protection — production sets API_KEY_PEPPER.
    api_key_pepper: str = Field(default="", alias="API_KEY_PEPPER")

    # security: per-account limits in Redis. 0 disables a limit.
    rate_limit_create_per_min: int = Field(default=60, alias="RATE_LIMIT_CREATE_PER_MIN")
    max_active_tasks_per_account: int = Field(default=100, alias="MAX_ACTIVE_TASKS_PER_ACCOUNT")
    max_daily_tasks: int = Field(default=10_000, alias="MAX_DAILY_TASKS")
    max_daily_spend: float = Field(default=50.0, alias="MAX_DAILY_SPEND")

    # 2captcha pingback (callbackUrl)
    callback_timeout_sec: float = Field(default=10.0, alias="CALLBACK_TIMEOUT_SEC")
    callback_max_attempts: int = Field(default=3, alias="CALLBACK_MAX_ATTEMPTS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
