from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.skill import UserSkill
from app.models.user import User
from app.schemas.skill import UserSkillResponse


router = APIRouter(prefix="/api/v1/skills", tags=["Skills"])


@router.get("/me", response_model=list[UserSkillResponse])
def list_my_skills(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserSkill]:
    return list(
        db.scalars(
            select(UserSkill)
            .where(UserSkill.user_id == current_user.id)
            .order_by(UserSkill.earned_at.desc())
        ).all()
    )