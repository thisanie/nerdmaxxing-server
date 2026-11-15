from sqlalchemy import or_

from app.core.time import utcnow
from app.models.challenge import Challenge


def public_challenge_conditions():
    return (
        Challenge.status == "PUBLISHED",
        Challenge.visibility == "PUBLIC",
        or_(Challenge.published_at.is_(None), Challenge.published_at <= utcnow()),
    )