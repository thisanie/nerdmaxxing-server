from app.services.rank_service import rank_for_aura
from app.services.aura_service import calculate_aura


def test_rank_thresholds_and_s_behavior():
    expected = {
        0: ("E", "D", 10_000),
        9_999: ("E", "D", 1),
        10_000: ("D", "C", 15_000),
        24_999: ("D", "C", 1),
        25_000: ("C", "B", 25_000),
        50_000: ("B", "A", 25_000),
        75_000: ("A", "S", 25_000),
    }
    for aura, values in expected.items():
        summary = rank_for_aura(aura)
        assert (summary.rank, summary.next_rank, summary.aura_to_next_rank) == values

    assert rank_for_aura(99_999).next_rank == "S"
    assert rank_for_aura(100_000).rank == "S"
    assert rank_for_aura(100_000).progress == 100
    assert rank_for_aura(100_000).next_rank is None
    assert rank_for_aura(100_000).aura_to_next_rank == 0


def test_challenge_aura_uses_scaled_base_rewards():
    expected = {"EASY": 1000, "MEDIUM": 2000, "HARD": 3250, "EXTREME": 4250}
    for difficulty, aura in expected.items():
        challenge = type("Challenge", (), {
            "difficulty_level": difficulty,
            "estimated_effort_min_minutes": 60,
            "estimated_effort_max_minutes": 60,
        })()
        assert calculate_aura(challenge) == aura

    unknown_challenge = type("Challenge", (), {
        "difficulty_level": "UNRECOGNIZED",
        "estimated_effort_min_minutes": 60,
        "estimated_effort_max_minutes": 60,
    })()
    assert calculate_aura(unknown_challenge) == 1000
