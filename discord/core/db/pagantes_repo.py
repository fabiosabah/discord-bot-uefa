# -*- coding: utf-8 -*-
from datetime import datetime

from core.db.connection import get_connection
from core.db.season_repo import get_current_season


def add_pagante(discord_id: int, display_name: str, season: int | None = None, mmr: int | None = None) -> None:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pagantes (discord_id, display_name, season, mmr, registered_at) VALUES (?, ?, ?, ?, ?)",
            (discord_id, display_name, season, mmr, datetime.utcnow().isoformat()),
        )
        conn.commit()


def remove_pagante(discord_id: int, season: int | None = None) -> bool:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM pagantes WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        )
        conn.commit()
    return cursor.rowcount > 0


def is_pagante(discord_id: int, season: int | None = None) -> bool:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM pagantes WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
    return row is not None


def list_pagantes(season: int | None = None) -> list[dict]:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT discord_id, display_name, mmr, registered_at FROM pagantes WHERE season = ? ORDER BY registered_at",
            (season,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pagante_mmr(discord_id: int, season: int | None = None) -> int | None:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT mmr FROM pagantes WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
    return row["mmr"] if row else None


def clear_pagantes_db(season: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM pagantes WHERE season = ?", (season,))
        conn.commit()
    return cursor.rowcount


def get_pagantes_mmr_bulk(discord_ids: list[int], season: int | None = None) -> dict[int, int]:
    if not discord_ids:
        return {}
    if season is None:
        season = get_current_season()
    placeholders = ",".join("?" * len(discord_ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT discord_id, mmr FROM pagantes WHERE discord_id IN ({placeholders}) AND season = ? AND mmr IS NOT NULL",
            discord_ids + [season],
        ).fetchall()
    return {row["discord_id"]: row["mmr"] for row in rows}
