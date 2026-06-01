# -*- coding: utf-8 -*-
import math

from core.db.connection import get_connection


def get_current_season() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM bot_config WHERE key = 'current_season'"
        ).fetchone()
    return int(row["value"]) if row else 1


def set_current_season(season: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES ('current_season', ?)",
            (str(season),),
        )
        conn.commit()


def get_season_pts(season: int) -> tuple[int, int]:
    """Returns (win_pts, loss_pts) for the given season."""
    if season >= 3:
        return 30, 20
    if season == 2:
        return 2, 1
    return 3, 1


def compute_win_probability(mmr_a: int, mmr_b: int) -> float:
    """P(team A wins) given total MMR of each team."""
    return 1.0 / (1.0 + 10 ** ((mmr_b - mmr_a) / 10000))


def compute_elo_deltas(p_winner: float) -> tuple[int, int]:
    """Returns (win_delta, loss_delta) given the winner's win probability.

    win_delta  = round(45 - 30 * p_winner)
    loss_delta = -round(5 + 30 * p_loser)   where p_loser = 1 - p_winner
    """
    win = round(45 - 30 * p_winner)
    loss = -round(5 + 30 * (1.0 - p_winner))
    return win, loss


def get_next_season_match_id(season: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(season_match_id), 0) FROM matches WHERE season = ?",
            (season,),
        ).fetchone()
    return row[0] + 1
