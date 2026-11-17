from sqlalchemy import or_

from app.models.challenge import Challenge


def public_challenge_conditions():
    return (
        Challenge.status == "PUBLISHED",
        Challenge.visibility == "PUBLIC",
    )