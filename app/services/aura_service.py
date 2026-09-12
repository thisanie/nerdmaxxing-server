from sqlalchemy.orm import Session

from app.models.aura import AuraTransaction
from app.models.user import User


def calculate_aura(challenge) -> int:
    difficulty_base_aura = {
        "EASY": 20,
        "BEGINNER": 20,
        "MEDIUM": 40,
        "INTERMEDIATE": 40,
        "HARD": 65,
        "ADVANCED": 65,
        "EXTREME": 85,
    }
    base_aura = difficulty_base_aura.get(str(challenge.difficulty_level).upper(), 20)

    effort_min = challenge.estimated_effort_min_minutes
    effort_max = challenge.estimated_effort_max_minutes
    if effort_min is None and effort_max is None:
        effort_minutes = None
    elif effort_min is None:
        effort_minutes = effort_max
    elif effort_max is None:
        effort_minutes = effort_min
    else:
        effort_minutes = (effort_min + effort_max) / 2

    if effort_minutes is None:
        effort_multiplier = 1.0
    elif effort_minutes < 30:
        effort_multiplier = 0.8
    elif effort_minutes <= 60:
        effort_multiplier = 1.0
    elif effort_minutes <= 120:
        effort_multiplier = 1.1
    elif effort_minutes <= 300:
        effort_multiplier = 1.2
    elif effort_minutes <= 600:
        effort_multiplier = 1.3
    else:
        effort_multiplier = 1.4

    return min(round(base_aura * effort_multiplier), 100)


def award_aura(
    db: Session,
    user: User,
    amount: int,
    reason: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
) -> AuraTransaction:
    transaction = AuraTransaction(
        user_id=user.id,
        amount=amount,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    user.aura_points += amount
    db.add(transaction)
    return transaction