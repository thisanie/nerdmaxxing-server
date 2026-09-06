from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_current_user
from app.models.user import User
from app.schemas.challenge import CategoryResponse
from app.schemas.discover import DiscoverResponse
from app.services.discovery_service import (
    get_categories,
    get_featured,
    get_legendary,
    get_new,
    get_recommended,
    get_trending,
    get_unexpected,
)


router = APIRouter(prefix="/api/v1", tags=["Discovery"])
optional_bearer = HTTPBearer(auto_error=False)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    if credentials is None:
        return None
    return get_current_user(
        credentials=credentials,
        db=db,
    )


@router.get("/categories", response_model=list[CategoryResponse])
def list_categories(db: Session = Depends(get_db)):
    return get_categories(db)


@router.get("/discover", response_model=DiscoverResponse)
def discover(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> DiscoverResponse:
    featured = get_featured(db)
    return DiscoverResponse(
        featured=featured,
        trending=get_trending(db),
        categories=get_categories(db),
        new_challenges=get_new(db),
        recommended=get_recommended(db, current_user) if current_user else [],
        legendary=get_legendary(db),
        unexpected=get_unexpected(db, exclude_id=featured.id if featured else None),
    )


@router.get("/discover/recommended")
def recommended(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_recommended(db, current_user)
