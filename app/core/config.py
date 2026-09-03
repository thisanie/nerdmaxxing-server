from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    google_web_client_id:str
    database_url:str

    model_config = SettingsConfigDict(
            env_file = ".env",
            case_sensitive = False
    )




settings = Settings()
