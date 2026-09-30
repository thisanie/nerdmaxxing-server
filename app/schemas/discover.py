from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.challenge import CategoryResponse, ChallengeResponse


class DiscoverResponse(BaseModel):
    featured: ChallengeResponse | None = None
    trending: list[ChallengeResponse]
    categories: list[CategoryResponse]
    new_challenges: list[ChallengeResponse]
    recommended: list[ChallengeResponse]
    legendary: list[ChallengeResponse]
    unexpected: list[ChallengeResponse]
    top_nerds: list["TopNerdResponse"] = Field(default_factory=list)
    recent_activity: list["RecentActivityResponse"] = Field(default_factory=list)


class TopNerdResponse(BaseModel):
    rank: int
    user_id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    completed_count: int
    day_streak: int


class RecentActivityResponse(BaseModel):
    id: str
    user_id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    action: str
    challenge_id: str | None
    challenge_title: str | None
    challenge_slug: str | None
    created_at: datetime


class DiscoverUserResult(BaseModel):
    user_id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None


class DiscoverSearchResponse(BaseModel):
    users: list[DiscoverUserResult]
    challenges: list[ChallengeResponse]
    total_users: int
    total_challenges: int
