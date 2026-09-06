from pydantic import BaseModel

from app.schemas.challenge import CategoryResponse, ChallengeResponse


class DiscoverResponse(BaseModel):
    featured: ChallengeResponse | None = None
    trending: list[ChallengeResponse]
    categories: list[CategoryResponse]
    new_challenges: list[ChallengeResponse]
    recommended: list[ChallengeResponse]
    legendary: list[ChallengeResponse]
    unexpected: list[ChallengeResponse]
