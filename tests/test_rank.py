from app.services.rank_service import rank_for_aura


def test_rank_thresholds_and_s_behavior():
    expected = {
        0: ("E", "D", 100),
        99: ("E", "D", 1),
        100: ("D", "C", 150),
        249: ("D", "C", 1),
        250: ("C", "B", 250),
        500: ("B", "A", 500),
        1000: ("A", "S", 1000),
    }
    for aura, values in expected.items():
        summary = rank_for_aura(aura)
        assert (summary.rank, summary.next_rank, summary.aura_to_next_rank) == values

    assert rank_for_aura(1999).next_rank == "S"
    assert rank_for_aura(2000).rank == "S"
    assert rank_for_aura(2000).progress == 100
    assert rank_for_aura(2000).next_rank is None
    assert rank_for_aura(2000).aura_to_next_rank == 0
