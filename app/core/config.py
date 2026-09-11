from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    google_web_client_id:str

    database_url:str

    s3_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ENDPOINT_URL_S3", "S3_ENDPOINT_URL"),
    )
    s3_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NEON_STORAGE_BUCKET", "S3_BUCKET"),
    )
    s3_access_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID"),
    )
    s3_secret_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"),
    )
    s3_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("AWS_REGION", "S3_REGION"),
    )
    s3_public_url: str | None = None

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
    default_challenge_image_url: str = "https://hips.hearstapps.com/hmg-prod/images/bright-forget-me-nots-royalty-free-image-1677788394.jpg"
    default_challenge_image_key: str = "defaults/challenge-cover.webp"

    model_config = SettingsConfigDict(
            env_file = ".env",
            case_sensitive = False
    )




settings = Settings()
