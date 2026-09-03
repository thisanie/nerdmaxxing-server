from fastapi import APIRouter, HTTPException

from app.schemas.auth import GoogleAuthRequest
from app.services.google_auth import verify_google_id_token


router = APIRouter(
        prefix = "/api/v1/auth",
        tags = ["Authentication"]
)


@router.post("/google")
def google_auth(request:GoogleAuthRequest):
    
    try:
        google_user = verify_google_id_token(request.id_token)
        return {
                "valid": True,
                "googel_user_id": google_user["sub"],
                "email": google_user.get("email"),
                "email_verified": google_user.get("email_verified"),
                "name": google_user.get("name"),
                "picture": google_user.get("picture"),
                }

    except ValueError:
        raise HTTPException(
                status = 401,
                detail = "Invalid Google ID token"
                )
