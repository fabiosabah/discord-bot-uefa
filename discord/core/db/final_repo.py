# -*- coding: utf-8 -*-
from datetime import datetime

from core.db.connection import get_connection
from core.db.season_repo import get_current_season


def set_final_participants(players: list[dict]) -> None:
    season = get_current_season()
    with get_connection() as conn:
        conn.execute("DELETE FROM final_participants WHERE season = ?", (season,))
        for p in players:
            conn.execute(
                "INSERT OR REPLACE INTO final_participants (discord_id, display_name, season, added_at) VALUES (?, ?, ?, ?)",
                (p["discord_id"], p["display_name"], season, datetime.utcnow().isoformat()),
            )
        conn.commit()


def get_final_participants(season: int | None = None) -> list[dict]:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT discord_id, display_name FROM final_participants WHERE season = ? ORDER BY added_at",
            (season,),
        ).fetchall()
    return [dict(r) for r in rows]


def is_final_participant(discord_id: int, season: int | None = None) -> bool:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM final_participants WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
    return row is not None
