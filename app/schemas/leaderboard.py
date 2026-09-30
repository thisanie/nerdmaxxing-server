from pydantic import BaseModel


class LeaderboardEntryResponse(BaseModel):
    rank: int
    user_id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    player_rank: str
    aura_points: int
    completed_challenge_count: int
    day_streak: int
    metric_value: int
    is_current_user: bool


class LeaderboardViewerResponse(BaseModel):
    rank: int
    metric_value: int
    user_id: str


class LeaderboardResponse(BaseModel):
    period: str
    metric: str
    entries: list[LeaderboardEntryResponse]
    viewer: LeaderboardViewerResponse | None
    total: int
