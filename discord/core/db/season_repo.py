# -*- coding: utf-8 -*-
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


def get_next_season_match_id(season: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(season_match_id), 0) FROM matches WHERE season = ?",
            (season,),
        ).fetchone()
    return row[0] + 1
