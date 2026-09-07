from sqlalchemy.orm import Session

from app.models.aura import AuraTransaction
from app.models.user import User


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