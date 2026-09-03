from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserSkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    source_challenge_id: str
    skill_name: str
    verification_status: str
    earned_at: datetime