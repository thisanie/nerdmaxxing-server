from pydantic import BaseModel


class UsernameRequest(BaseModel):
    username: str


class UsernameResponse(BaseModel):
    username: str


class UsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool
