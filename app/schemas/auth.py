from pydantic import BaseModel

class GoogleAuthRequest(BaseModel):
    id_token : str


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
    is_new_user: bool
    display_name: str | None
    avatar_url: str | None





