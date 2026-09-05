from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    google_web_client_id:str

    database_url:str

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

    model_config = SettingsConfigDict(
            env_file = ".env",
            case_sensitive = False
    )




settings = Settings()
