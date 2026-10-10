from dataclasses import dataclass
from datetime import datetime

import requests

from app.core.config import settings


class ChessComError(Exception):
    """Base error for failures talking to Chess.com's documented public API."""


class ChessComNotFound(ChessComError):
    pass


class ChessComRateLimited(ChessComError):
    pass


class ChessComUnavailable(ChessComError):
    pass


class ChessComMalformedResponse(ChessComError):
    pass


@dataclass(frozen=True)
class ChessComProfile:
    player_id: str
    username: str
    avatar_url: str | None
    location: str | None


@dataclass(frozen=True)
class ChessComRapidRating:
    value: int
    observed_at: datetime


def _get(path: str) -> dict:
    try:
        response = requests.get(
            f"https://api.chess.com/pub/{path}",
            headers={"User-Agent": settings.chess_com_user_agent},
            timeout=settings.chess_com_api_timeout_seconds,
        )
    except requests.RequestException as error:
        raise ChessComUnavailable("Chess.com API is unavailable.") from error
    if response.status_code == 404:
        raise ChessComNotFound("Chess.com player was not found.")
    if response.status_code == 429:
        raise ChessComRateLimited("Chess.com API rate limit exceeded.")
    if response.status_code >= 500:
        raise ChessComUnavailable("Chess.com API is temporarily unavailable.")
    if response.status_code >= 400:
        raise ChessComError("Chess.com API request failed.")
    try:
        payload = response.json()
    except ValueError as error:
        raise ChessComMalformedResponse("Chess.com returned malformed data.") from error
    if not isinstance(payload, dict):
        raise ChessComMalformedResponse("Chess.com returned malformed data.")
    return payload


def fetch_profile(username: str) -> ChessComProfile:
    payload = _get(f"user/{username}")
    player_id = payload.get("player_id")
    canonical_username = payload.get("username")
    if not isinstance(player_id, (int, str)) or not isinstance(canonical_username, str):
        raise ChessComMalformedResponse("Chess.com profile is missing its identity.")
    return ChessComProfile(
        player_id=str(player_id),
        username=canonical_username,
        avatar_url=payload.get("avatar") if isinstance(payload.get("avatar"), str) else None,
        location=payload.get("location") if isinstance(payload.get("location"), str) else None,
    )


def fetch_rapid_rating(username: str) -> ChessComRapidRating:
    payload = _get(f"user/{username}/stats")
    rapid = payload.get("chess_rapid")
    if not isinstance(rapid, dict):
        raise ChessComMalformedResponse("Chess.com rapid statistics are unavailable.")
    rating = rapid.get("last", {}).get("rating") if isinstance(rapid.get("last"), dict) else None
    if not isinstance(rating, int) or isinstance(rating, bool) or rating < 0:
        raise ChessComMalformedResponse("Chess.com rapid rating is unavailable.")
    return ChessComRapidRating(value=rating, observed_at=datetime.utcnow())
