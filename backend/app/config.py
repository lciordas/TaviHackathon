from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"
    cors_origins: list[str] = ["http://localhost:3000"]

    google_places_api_key: str | None = None
    google_places_default_radius_m: int = 32186  # ~20 miles

    bbb_user_agent: str = "TaviBot/0.1 (hackathon research)"
    bbb_request_delay_s: float = 1.0

    # MailPit — local email bus. Tavi sends via SMTP; vendor simulators
    # read via the HTTP API. See CLAUDE.md for the runtime setup.
    mailpit_enabled: bool = True
    mailpit_smtp_host: str = "localhost"
    mailpit_smtp_port: int = 1025
    mailpit_api_base: str = "http://localhost:8025"
    tavi_email_domain: str = "tavi.local"  # Tavi = tavi+{work_order_id}@{domain}


settings = Settings()
