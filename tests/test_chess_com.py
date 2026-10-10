from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.integrations import confirm_chess_com_verification
from app.services import chess_com


def test_profile_and_rapid_rating_are_parsed_from_public_api(monkeypatch):
    responses = [
        SimpleNamespace(
            status_code=200,
            json=lambda: {"player_id": 42, "username": "Player", "location": "NMX-ABC123"},
        ),
        SimpleNamespace(
            status_code=200,
            json=lambda: {"chess_rapid": {"last": {"rating": 1234}}},
        ),
    ]
    monkeypatch.setattr(chess_com.requests, "get", lambda *args, **kwargs: responses.pop(0))
    profile = chess_com.fetch_profile("player")
    rating = chess_com.fetch_rapid_rating("player")
    assert profile.player_id == "42"
    assert profile.location == "NMX-ABC123"
    assert rating.value == 1234


def test_malformed_rating_is_not_treated_as_zero(monkeypatch):
    monkeypatch.setattr(
        chess_com.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {"chess_rapid": {}}),
    )
    with pytest.raises(chess_com.ChessComMalformedResponse):
        chess_com.fetch_rapid_rating("player")


def test_confirmation_rejects_expired_challenge():
    challenge = SimpleNamespace(
        id="challenge",
        user_id="user",
        provider_id="chess_com",
        consumed_at=None,
        expires_at=datetime.utcnow() - timedelta(seconds=1),
        attempt_count=0,
        secret_hash="anything",
    )

    class Db:
        def scalar(self, statement):
            return challenge

    with pytest.raises(HTTPException) as error:
        confirm_chess_com_verification(
            SimpleNamespace(challenge_id="challenge", verification_code="NMX-ABC123"),
            Db(),
            SimpleNamespace(id="user"),
        )
    assert error.value.status_code == 410
