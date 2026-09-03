from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import Identity
from app.models.user import User


def get_or_create_google_user(
  db:Session,
  *,
  google_user_id: str,
  email:str | None,
  name: str | None,
  picture:str | None,
)->tuple[User, bool]:
  #look for an existing google identity
  statement = (
    select(Identity).where(Identity.provider == "google", Identity.provider_usr_id == google_user_id,
                          )
  )

identity = db.scalar(statement)

#existing user
if identity is not None:
  return identity.user, False

#new user
user = User(
  username = None,
  username_normalized = None,
  display_name = name,
  avatar_url = picture,
)

db.add(user)
#generate user_id without commiting yet hence flush
db.flush()

identity = Identity(
  user_id = user.id,
  provider = "google",
  provider_user_id = google_user_id,
  provider_email = email,
)

db.add(identity)
db.commit()
db.refresh(user)

return user, True
