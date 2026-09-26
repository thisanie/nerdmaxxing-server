from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ParticipationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    challenge_id: str
    user_id: str
    status: str
    completion_status: str
    verification_status: str
    started_at: datetime
    last_activity_at: datetime
    completed_at: datetime | None


class ParticipationStatusUpdate(BaseModel):
    status: str


class ProgressLogCreate(BaseModel):
    hours_spent: float | None = Field(default=None, gt=0, le=24)
    metrics: dict[str, float | bool] = Field(default_factory=dict, max_length=20)
    note: str | None = Field(default=None, max_length=5000)


class ProgressLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    participant_id: str
    user_id: str
    minutes_spent: int
    note: str | None
    metrics: dict[str, float | bool]
    created_at: datetime

    @field_validator("metrics", mode="before")
    @classmethod
    def normalize_metrics(cls, value):
        return value or {}


class ResourceCompletionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_minutes: int | None = Field(default=None, ge=0, le=1440)
    note: str | None = Field(default=None, max_length=5000)
    log_progress: bool = True


class ResourceCompletionResponse(BaseModel):
    resource_id: str
    milestone_id: str
    completed: bool
    completed_at: datetime
    resource_minutes: int | None
    milestone_minutes: int
    note: str | None
    milestone_status: str
    milestone_completed: bool
    challenge_status: str
    ready_for_proof: bool


class ResourceCompletionStatusResponse(BaseModel):
    resource_id: str
    milestone_id: str
    completed: bool
    completed_at: datetime | None
    resource_minutes: int | None
    milestone_minutes: int
    note: str | None
    milestone_status: str
    milestone_completed: bool
    challenge_status: str
    ready_for_proof: bool


class MetricAttemptCreate(BaseModel):
    metric_key: str = Field(min_length=1, max_length=50)
    value: float | bool
    unit: str | None = Field(default=None, max_length=30)
    note: str | None = Field(default=None, max_length=5000)


class MetricAttemptResponse(BaseModel):
    id: str
    metric_key: str
    value: float | bool
    unit: str | None
    note: str | None
    created_at: datetime
    meets_target: bool