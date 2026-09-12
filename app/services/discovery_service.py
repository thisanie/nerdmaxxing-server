from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.category import Category
from app.models.challenge import Challenge, challenge_categories
from app.models.participation import ChallengeParticipant
from app.models.user import User
from app.schemas.challenge import CategoryResponse, ChallengeResponse
from app.services.aura_service import calculate_aura


def _public_query():
    return (
        select(Challenge)
        .where(Challenge.status == "PUBLISHED", Challenge.visibility == "PUBLIC")
        .options(selectinload(Challenge.categories), selectinload(Challenge.resources))
    )


def _challenge_response(
    challenge: Challenge,
    enrollment_count: int = 0,
    completion_count: int = 0,
) -> ChallengeResponse:
    response = ChallengeResponse.model_validate(challenge)
    response.image_key = challenge.image_key or settings.default_challenge_image_key
    response.aura_points = calculate_aura(challenge)
    response.enrollment_count = enrollment_count
    response.completion_count = completion_count
    return response


def _responses(challenges: list[Challenge], db: Session) -> list[ChallengeResponse]:
    challenge_ids = [challenge.id for challenge in challenges]
    if not challenge_ids:
        return []
    enrollment_counts = dict(
        db.execute(
            select(ChallengeParticipant.challenge_id, func.count())
            .where(ChallengeParticipant.challenge_id.in_(challenge_ids))
            .group_by(ChallengeParticipant.challenge_id)
        ).all()
    )
    completion_counts = dict(
        db.execute(
            select(ChallengeParticipant.challenge_id, func.count())
            .where(
                ChallengeParticipant.challenge_id.in_(challenge_ids),
                ChallengeParticipant.completion_status == "COMPLETED",
            )
            .group_by(ChallengeParticipant.challenge_id)
        ).all()
    )
    return [
        _challenge_response(
            challenge,
            enrollment_counts.get(challenge.id, 0),
            completion_counts.get(challenge.id, 0),
        )
        for challenge in challenges
    ]


def get_categories(db: Session) -> list[CategoryResponse]:
    categories = db.scalars(
        select(Category).where(Category.is_active.is_(True)).order_by(Category.display_order, Category.name)
    ).all()
    return [CategoryResponse.model_validate(category) for category in categories]


def get_featured(db: Session) -> ChallengeResponse | None:
    challenge = db.scalar(
        _public_query()
        .where(Challenge.featured.is_(True))
        .order_by(Challenge.published_at.desc(), Challenge.created_at.desc())
        .limit(1)
    )
    return _responses([challenge], db)[0] if challenge else None


def get_trending(db: Session, limit: int = 10) -> list[ChallengeResponse]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    challenges = db.scalars(
        _public_query()
        .join(ChallengeParticipant, ChallengeParticipant.challenge_id == Challenge.id)
        .where(ChallengeParticipant.started_at >= cutoff)
        .group_by(*Challenge.__table__.columns)
        .order_by(func.count(ChallengeParticipant.id).desc(), Challenge.published_at.desc())
        .limit(limit)
    ).unique().all()
    if challenges:
        return _responses(list(challenges), db)
    fallback = db.scalars(
        _public_query()
        .order_by(Challenge.published_at.desc(), Challenge.created_at.desc())
        .limit(limit)
    ).all()
    return _responses(list(fallback), db)


def get_new(db: Session, limit: int = 10) -> list[ChallengeResponse]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    challenges = db.scalars(
        _public_query()
        .where(Challenge.published_at >= cutoff)
        .order_by(Challenge.published_at.desc(), Challenge.created_at.desc())
        .limit(limit)
    ).all()
    if challenges:
        return _responses(list(challenges), db)
    fallback = db.scalars(
        _public_query()
        .order_by(Challenge.published_at.desc(), Challenge.created_at.desc())
        .limit(limit)
    ).all()
    return _responses(list(fallback), db)


def get_legendary(db: Session, limit: int = 10) -> list[ChallengeResponse]:
    challenges = db.scalars(
        _public_query()
        .where(Challenge.legendary.is_(True))
        .order_by(Challenge.difficulty_level.desc(), Challenge.published_at.desc())
        .limit(limit)
    ).all()
    return _responses(list(challenges), db)


def get_unexpected(db: Session, limit: int = 10, exclude_id: str | None = None) -> list[ChallengeResponse]:
    statement = _public_query()
    if exclude_id:
        statement = statement.where(Challenge.id != exclude_id)
    challenges = db.scalars(statement.order_by(func.random()).limit(limit)).all()
    return _responses(list(challenges), db)


def get_recommended(db: Session, user: User, limit: int = 10) -> list[ChallengeResponse]:
    completed_category_ids = (
        select(challenge_categories.c.category_id)
        .join(ChallengeParticipant, ChallengeParticipant.challenge_id == challenge_categories.c.challenge_id)
        .where(
            ChallengeParticipant.user_id == user.id,
            ChallengeParticipant.completion_status == "COMPLETED",
        )
    )
    already_participating = exists().where(
        ChallengeParticipant.user_id == user.id,
        ChallengeParticipant.challenge_id == Challenge.id,
    )
    popularity = (
        select(func.count(ChallengeParticipant.id))
        .where(ChallengeParticipant.challenge_id == Challenge.id)
        .scalar_subquery()
    )
    matching_category = exists().where(
        challenge_categories.c.challenge_id == Challenge.id,
        challenge_categories.c.category_id.in_(completed_category_ids),
    )
    challenges = db.scalars(
        _public_query()
        .where(
            matching_category,
            ~already_participating,
        )
        .order_by(popularity.desc(), Challenge.published_at.desc())
        .limit(limit)
    ).all()
    return _responses(list(challenges), db)
