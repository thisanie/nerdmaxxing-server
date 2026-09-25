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