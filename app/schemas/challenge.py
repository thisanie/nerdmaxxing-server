from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class ChallengeCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    image_url: AnyHttpUrl
    resources: list["ChallengeResourceCreate"] = Field(min_length=1, max_length=20)
    short_description: str = Field(min_length=1, max_length=300)
    full_description: str = Field(min_length=1)
    difficulty_level: str = Field(default="BEGINNER", min_length=1, max_length=30)
    estimated_effort_min_minutes: int | None = Field(default=None, ge=1)
    estimated_effort_max_minutes: int | None = Field(default=None, ge=1)
    verification_type: str = Field(default="SELF_REPORTED", min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_effort_range(self) -> "ChallengeCreate":
        if (
            self.estimated_effort_min_minutes is not None
            and self.estimated_effort_max_minutes is not None
            and self.estimated_effort_min_minutes > self.estimated_effort_max_minutes
        ):
            raise ValueError("Minimum effort cannot exceed maximum effort.")
        return self


class ChallengeResourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    url: AnyHttpUrl
    resource_type: str = Field(default="LINK", min_length=1, max_length=30)
    rationale: str = Field(min_length=1, max_length=1000)


class ChallengeResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    url: AnyHttpUrl
    resource_type: str
    rationale: str
    order_index: int


class ChallengeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    image_url: AnyHttpUrl
    resources: list[ChallengeResourceResponse]
    slug: str
    short_description: str
    full_description: str
    creator_id: str
    difficulty_level: str
    status: str
    visibility: str
    estimated_effort_min_minutes: int | None
    estimated_effort_max_minutes: int | None
    verification_type: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None