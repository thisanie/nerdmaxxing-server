from fastapi import APIRouter, HTTPException, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.schemas.auth import GoogleAuthRequest, GoogleAuthResponse, RefreshTokenRequest, TokenPair
from app.services.google_auth import verify_google_id_token
from app.services.user_service import get_or_create_google_user
from app.services.session_service import create_session, revoke_refresh_token, rotate_refresh_token
from app.services.token_service import create_access_token
from app.core.config import settings
from app.core.dependencies import get_db, limiter



router = APIRouter(
        prefix = "/api/v1/auth",
        tags = ["Authentication"]
)


@router.post("/google")
@limiter.limit(settings.google_auth_rate_limit)
def google_auth(request: Request, payload: GoogleAuthRequest, db:Session = Depends(get_db)):
    
   try:
      google_user = verify_google_id_token(payload.id_token)
   except ValueError:
      raise HTTPException(
              status_code=401,
              detail="Invalid Google ID token"
              )

   if google_user.get("email_verified") is not True:
      raise HTTPException(
              status_code=401,
              detail="Google account email must be verified"
              )

   user, is_new_user = get_or_create_google_user(
      db =db, 
      google_user_id = google_user["sub"],
      email = google_user.get("email"),
      name = google_user.get("name"),
      picture= google_user.get("picture")

      )

   session, refresh_token = create_session(
      db = db,
      user_id = user.id
   )

   access_token = create_access_token(
      user_id= user.id,
      session_id= session.id,
   )

   return GoogleAuthResponse(
      access_token= access_token,
      refresh_token=refresh_token,
      user_id = user.id,
      username= user.username,
      needs_username= user.username is None,
      is_new_user=is_new_user,
      display_name= user.display_name,
      avatar_url= user.avatar_url
   )


@router.post("/refresh", response_model= TokenPair)
@limiter.limit(settings.refresh_auth_rate_limit)
def refresh_auth(request: Request, payload: RefreshTokenRequest, db:Session = Depends(get_db)):
   result = rotate_refresh_token(db= db, refresh_token=payload.refresh_token)
   if result is None:
      raise HTTPException(
        status_code=401,
        detail = "Invalid, expired, or revoked refresh token",
      )

   session, new_refresh_token = result

   access_token = create_access_token(
      user_id=session.user_id,
      session_id= session.id,
   )

   return TokenPair(
      access_token=access_token,
      refresh_token=new_refresh_token
   )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: RefreshTokenRequest, db:Session = Depends(get_db)) -> Response:
   revoke_refresh_token(db=db, refresh_token=request.refresh_token)
   return Response(status_code=status.HTTP_204_NO_CONTENT)
