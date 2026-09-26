from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings


class ChallengeMetricDefinition(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=50)
    label: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=30)
    unit: str | None = Field(default=None, max_length=30)
    target: float | bool | None = None
    baseline: float | bool | None = None
    direction: str = Field(default="AT_LEAST", pattern="^(AT_LEAST|AT_MOST|EXACTLY|BOOLEAN)$")
    is_primary: bool = False
    format: str = Field(default="DECIMAL_2", min_length=1, max_length=30)


class ChallengeRequirement(BaseModel):
    metric_key: str = Field(min_length=1, max_length=50)
    operator: str = Field(pattern="^(AT_LEAST|AT_MOST|EXACTLY|BOOLEAN)$")
    value: float | bool
    unit: str | None = Field(default=None, max_length=30)
    label: str = Field(min_length=1, max_length=200)


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
    metrics: list[ChallengeMetricDefinition] = Field(default_factory=list, max_length=20)
    requirements: list[ChallengeRequirement] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_effort_range(self) -> "ChallengeCreate":
        if (
            self.estimated_effort_min_minutes is not None
            and self.estimated_effort_max_minutes is not None
            and self.estimated_effort_min_minutes > self.estimated_effort_max_minutes
        ):
            raise ValueError("Minimum effort cannot exceed maximum effort.")
        keys = {metric.key for metric in self.metrics}
        if len(keys) != len(self.metrics):
            raise ValueError("Metric keys must be unique.")
        if sum(metric.is_primary for metric in self.metrics) > 1:
            raise ValueError("Only one metric can be primary.")
        if any(requirement.metric_key not in keys for requirement in self.requirements):
            raise ValueError("Requirements must reference a defined metric.")
        metric_by_key = {metric.key: metric for metric in self.metrics}
        for requirement in self.requirements:
            metric = metric_by_key[requirement.metric_key]
            if requirement.unit != metric.unit:
                raise ValueError(f"Requirement unit must match metric '{metric.key}'.")
            if metric.kind == "PERCENTAGE" and not 0 <= requirement.value <= 100:
                raise ValueError(f"Percentage requirement '{metric.key}' must be between 0 and 100.")
        for metric in self.metrics:
            if metric.kind == "PERCENTAGE" and metric.target is not None and not 0 <= metric.target <= 100:
                raise ValueError(f"Percentage metric '{metric.key}' must be between 0 and 100.")
            if isinstance(metric.target, (int, float)) and metric.target < 0:
                raise ValueError(f"Metric target '{metric.key}' cannot be negative.")
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
    metrics: list[ChallengeMetricDefinition] = Field(default_factory=list)
    requirements: list[ChallengeRequirement] = Field(default_factory=list)

    @field_validator("metrics", "requirements", mode="before")
    @classmethod
    def normalize_metric_configuration(cls, value):
        return value or []

    @field_validator("image_url", mode="before")
    @classmethod
    def use_default_image(cls, value: str | None) -> str:
        return value or settings.default_challenge_image_url


class ChallengeProgressResponse(BaseModel):
    metrics: list[dict]


class ChallengeMilestoneResponse(BaseModel):
    id: str
    order_index: int
    title: str
    description: str
    status: str
    current_value: float
    target_value: float
    resources: list[dict] = Field(default_factory=list)

    @field_validator("resources", mode="before")
    @classmethod
    def normalize_resources(cls, value):
        return value or []


class ChallengeAttemptResponse(BaseModel):
    id: str
    metrics: dict[str, float | bool]
    created_at: datetime

    @field_validator("metrics", mode="before")
    @classmethod
    def normalize_attempt_metrics(cls, value):
        return value or {}


class ChallengeParticipantPreviewResponse(BaseModel):
    user_id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    status: str
    completed_at: datetime | None


class ChallengeStatsResponse(BaseModel):
    participant_count: int
    completed_participant_count: int


class ChallengeVerificationResponse(BaseModel):
    type: str
    requirements: list[ChallengeRequirement]
    required_runs: int
    instructions: str | None


class ChallengeDetailResponse(BaseModel):
    challenge: ChallengeResponse
    stats: ChallengeStatsResponse
    metrics: list[dict]
    requirements: list[ChallengeRequirement]
    milestones: list[ChallengeMilestoneResponse]
    attempts: list[ChallengeAttemptResponse]
    participants: list[ChallengeParticipantPreviewResponse]
    verification: ChallengeVerificationResponse


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None = None
    icon: str | None = None