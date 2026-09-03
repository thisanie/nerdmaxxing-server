from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.schemas.auth import GoogleAuthRequest, GoogleAuthResponse

from app.services.google_aut import verify_google_id_token
from app.services.user_service import get_or_create_google_user

router = APIRouter(
  prefix = "/api/v1/auth",
  tags = ["Authentication"]
)

@router.post("/google", response_model = GoogleAuthResponse)
def google_auth(
  request: GoogleAuthRequest,
  db:Session = Depends(get_db)
):

  try:
    google_user = verify_google_id_token(request.id_token)
  except ValueError:
    raise HTTPException(
      status_code = 401,
      detail = "Invalid Google ID token"
    )

  user, is_new_user = get_or_create_google_user(
    db = db,
    google_user_id = google_user["sub"],
    email = google_user.get("email"),
    name = google_user.get("picture"),
  )

  return GoogleAuthResponse(
    user_id = user.id,
    username = user.username,
    needs_username = user.username is None,
    is_new_user = is_new_user,
    display_name = user.display_name,
    avatar_url = user.avatar_url,
  )



    
