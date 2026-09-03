from sqlachemy import select
from sqlalchemy.orm import Session

from app.models.identity import Identity
from app.models.user import User


def get_or_create_google_user(
        db:Session,
        *,
        google_user_id: str,
        email:str | None,
        name: str | None,
        picture: str | None,
) ->tuple[User, bool]:
    