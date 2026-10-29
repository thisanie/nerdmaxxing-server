from pydantic import BaseModel, Field, SecretStr

class GoogleAuthRequest(BaseModel):
    id_token : str


class PasswordSetupRequest(BaseModel):
    password: SecretStr = Field(min_length=12, max_length=128)


class PasswordLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=24)
    password: SecretStr = Field(min_length=1, max_length=128)


class RefreshTokenRequest(BaseModel):
    refresh_token:str


class TokenPair(BaseModel):
    access_token: str
    refresh_token:str
    token_type: str = "bearer"

class GoogleAuthResponse(TokenPair):
    user_id:str
    username:str | None
    needs_username: bool
    needs_password: bool
    is_new_user: bool
    display_name: str | None
    avatar_url: str | None





