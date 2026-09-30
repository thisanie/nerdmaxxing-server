from dataclasses import dataclass


RANK_THRESHOLDS = (
    ("E", 0, 100),
    ("D", 100, 250),
    ("C", 250, 500),
    ("B", 500, 1000),
    ("A", 1000, 2000),
    ("S", 2000, None),
)


@dataclass(frozen=True)
class RankSummary:
    rank: str
    progress: int
    next_rank: str | None
    aura_to_next_rank: int


def rank_for_aura(aura_points: int) -> RankSummary:
    aura_points = max(aura_points, 0)
    for index, (rank, minimum, maximum) in enumerate(RANK_THRESHOLDS):
        if maximum is None or aura_points < maximum:
            if maximum is None:
                return RankSummary(rank, 100, None, 0)
            span = maximum - minimum
            progress = min(100, max(0, round((aura_points - minimum) * 100 / span)))
            next_rank = RANK_THRESHOLDS[index + 1][0]
            return RankSummary(rank, progress, next_rank, maximum - aura_points)
    return RankSummary("S", 100, None, 0)
