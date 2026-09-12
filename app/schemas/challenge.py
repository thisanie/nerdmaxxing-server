from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings


class ChallengeCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    image_url: AnyHttpUrl | None = None
    resources: list["ChallengeResourceCreate"] = Field(min_length=1, max_length=20)
    short_description: str = Field(min_length=1, max_length=300)
    full_description: str = Field(min_length=1)
    difficulty_level: str = Field(default="BEGINNER", min_length=1, max_length=30)
    estimated_effort_min_minutes: int | None = Field(default=None, ge=1)
    estimated_effort_max_minutes: int | None = Field(default=None, ge=1)
    estimated_duration_minutes: int | None = Field(default=None, gt=0)
    category_ids: list[str] = Field(default_factory=list, max_length=3)
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
    url: AnyHttpUrl | None = None
    resource_type: str = Field(default="LINK", min_length=1, max_length=30)
    rationale: str = Field(min_length=1, max_length=1000)
    order_index: int = Field(default=0, ge=0)


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
    aura_points: int = 0
    status: str
    visibility: str
    estimated_effort_min_minutes: int | None
    estimated_effort_max_minutes: int | None
    estimated_duration_minutes: int | None
    image_key: str | None
    featured: bool
    legendary: bool
    categories: list["CategoryResponse"] = Field(default_factory=list)
    enrollment_count: int = 0
    completion_count: int = 0
    verification_type: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None

    @field_validator("image_url", mode="before")
    @classmethod
    def use_default_image(cls, value: str | None) -> str:
        return value or settings.default_challenge_image_url


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None = None
    icon: str | None = None