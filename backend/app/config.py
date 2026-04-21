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


settings = Settings()
