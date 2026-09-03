import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from app.core.config import settings

def create_access_token(
	*,
	user_id: str,
	session_id: str
)->str:

	now = datetime.now(timezone.utc)
	expires_at = now + timedelta(minutes = settings.access_token_expire_minutes)

	payload = {
		"sub": user_id,
		"sid": session_id,
		"iat": now,
		"exp": expires_at,
	}

  return jwt.encode(payload, settings.jwt_secret_Key, algorithm = settings.jwt_algorithm,)


def create_refresh_token()->str:
  return secrets.token_urlsafe(64)

def hash_refresh_token(refresh_token:str)-> str:
  return hashlib.sha256(refresh_token.encode()).hexdigest()

  
	
