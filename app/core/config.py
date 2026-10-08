from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    google_web_client_id:str

    database_url:str

    r2_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("R2_ENDPOINT_URL", "CLOUDFLARE_R2_URL"),
    )
    r2_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("R2_BUCKET", "CLOUDFLARE_R2_BUCKET"),
    )
    r2_access_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_ACCESS_KEY_ID"),
    )
    r2_secret_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("R2_SECRET_ACCESS_KEY", "CLOUDFLARE_R2_SECRET_ACCESS_KEY"),
    )
    r2_region: str = Field(default="auto", validation_alias="R2_REGION")
    r2_public_url: str | None = Field(default=None, validation_alias="R2_PUBLIC_URL")

    jwt_secret_key:str
    jwt_algorithm: str = "HS256"

    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    cors_allow_origins: list[str] = [
       "*"
    ]

    cors_allow_origin_regex: str | None = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    force_https: bool = False
    google_auth_rate_limit: str = "10/minute"
    refresh_auth_rate_limit: str = "30/minute"
    username_availability_rate_limit: str = "60/minute"
    discussion_post_rate_limit: str = "10/minute"
    default_challenge_image_url: str = "https://hips.hearstapps.com/hmg-prod/images/bright-forget-me-nots-royalty-free-image-1677788394.jpg"
    default_challenge_image_key: str = "defaults/challenge-cover.webp"
    app_base_url: str = "http://localhost:3000"

    firebase_project_id: str | None = None
    firebase_client_email: str | None = None
    firebase_private_key: str | None = None

    model_config = SettingsConfigDict(
            env_file = ".env",
            case_sensitive = False,
            extra = "ignore",
    )




settings = Settings()
