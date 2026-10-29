from fastapi import APIRouter, HTTPException, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.schemas.auth import (
   GoogleAuthRequest,
   GoogleAuthResponse,
   PasswordLoginRequest,
   PasswordSetupRequest,
   PasswordStatusResponse,
   PasswordUpdateRequest,
   RefreshTokenRequest,
   TokenPair,
)
from app.services.google_auth import verify_google_id_token
from app.services.user_service import get_or_create_google_user
from app.services.session_service import create_session, revoke_refresh_token, rotate_refresh_token
from app.services.password_service import (
   authenticate_password_user,
   hash_password,
   verify_password,
)
from app.services.token_service import create_access_token
from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, limiter
from app.models.user import User



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
      needs_password= user.username is not None and user.password_hash is None,
      is_new_user=is_new_user,
      display_name= user.display_name,
      avatar_url= user.avatar_url
   )


@router.post("/password/setup", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.password_setup_rate_limit)
def setup_password(
   request: Request,
   payload: PasswordSetupRequest,
   db: Session = Depends(get_db),
   current_user: User = Depends(get_current_user),
) -> Response:
   if current_user.username is None:
      raise HTTPException(
         status_code=status.HTTP_409_CONFLICT,
         detail="Set a username before setting a password.",
      )
   if current_user.password_hash is not None:
      raise HTTPException(
         status_code=status.HTTP_409_CONFLICT,
         detail="A password is already set for this account.",
      )

   current_user.password_hash = hash_password(payload.password.get_secret_value())
   db.commit()
   return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/password/status", response_model=PasswordStatusResponse)
def password_status(
   current_user: User = Depends(get_current_user),
) -> PasswordStatusResponse:
   return PasswordStatusResponse(has_password=current_user.password_hash is not None)


@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.password_setup_rate_limit)
def update_password(
   request: Request,
   payload: PasswordUpdateRequest,
   db: Session = Depends(get_db),
   current_user: User = Depends(get_current_user),
) -> Response:
   if current_user.password_hash is None:
      raise HTTPException(
         status_code=status.HTTP_409_CONFLICT,
         detail="No password is set. Use the password setup endpoint first.",
      )
   if not verify_password(
      payload.current_password.get_secret_value(),
      current_user.password_hash,
   ):
      raise HTTPException(
         status_code=status.HTTP_401_UNAUTHORIZED,
         detail="Current password is incorrect.",
      )

   current_user.password_hash = hash_password(payload.new_password.get_secret_value())
   db.commit()
   return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/login", response_model=TokenPair)
@limiter.limit(settings.password_auth_rate_limit)
def password_login(
   request: Request,
   payload: PasswordLoginRequest,
   db: Session = Depends(get_db),
) -> TokenPair:
   user = authenticate_password_user(
      db,
      username=payload.username,
      password=payload.password.get_secret_value(),
   )
   if user is None:
      raise HTTPException(
         status_code=status.HTTP_401_UNAUTHORIZED,
         detail="Invalid username or password.",
         headers={"WWW-Authenticate": "Bearer"},
      )

   session, refresh_token = create_session(db=db, user_id=user.id)
   return TokenPair(
      access_token=create_access_token(user_id=user.id, session_id=session.id),
      refresh_token=refresh_token,
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
