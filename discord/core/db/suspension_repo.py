# -*- coding: utf-8 -*-
from datetime import datetime

from core.db.connection import get_connection
from core.db.bot_config_repo import get_completed_lobbies_count
from core.db.season_repo import get_current_season


def add_suspension(discord_id: int, display_name: str, reason: str, lists_duration: int, applied_by: int | None = None) -> None:
    lists_at_ban = get_completed_lobbies_count()
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO suspensions
               (discord_id, display_name, reason, suspended_at, suspended_until, applied_by, lists_at_ban, lists_duration)
               VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
            (discord_id, display_name, reason, datetime.utcnow().isoformat(), applied_by, lists_at_ban, lists_duration),
        )
        conn.commit()


def remove_suspension(discord_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM suspensions WHERE discord_id = ?", (discord_id,))
        conn.commit()
    return cursor.rowcount > 0


def is_suspended(discord_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT lists_at_ban, lists_duration FROM suspensions WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    if row is None:
        return False
    lists_at_ban = row["lists_at_ban"]
    lists_duration = row["lists_duration"]
    if lists_at_ban is None or lists_duration is None:
        return False
    current_count = get_completed_lobbies_count()
    return (current_count - lists_at_ban) < lists_duration


def get_suspension(discord_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM suspensions WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    if data.get("lists_at_ban") is not None and data.get("lists_duration") is not None:
        current_count = get_completed_lobbies_count()
        data["lists_remaining"] = data["lists_duration"] - (current_count - data["lists_at_ban"])
    return data


def list_suspensions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM suspensions ORDER BY suspended_at DESC"
        ).fetchall()
    result = []
    current_count = get_completed_lobbies_count()
    for row in rows:
        data = dict(row)
        if data.get("lists_at_ban") is not None and data.get("lists_duration") is not None:
            remaining = data["lists_duration"] - (current_count - data["lists_at_ban"])
            if remaining <= 0:
                continue
            data["lists_remaining"] = remaining
        result.append(data)
    return result


# ── Penalidades de pontos ──────────────────────────────────────────────────

def add_point_penalty(discord_id: int, display_name: str, points: int, reason: str, applied_by: int | None = None) -> None:
    season = get_current_season()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO point_penalties (discord_id, display_name, points, reason, season, applied_by, applied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (discord_id, display_name, -abs(points), reason, season, applied_by, datetime.utcnow().isoformat()),
        )
        conn.commit()


def remove_point_penalty(penalty_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM point_penalties WHERE id = ?", (penalty_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_point_penalties(season: int | None = None) -> list[dict]:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM point_penalties WHERE season = ? ORDER BY applied_at DESC",
            (season,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_point_penalties_sum_by_player(season: int | None = None) -> dict[int, int]:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT discord_id, SUM(points) AS total FROM point_penalties WHERE season = ? GROUP BY discord_id",
            (season,),
        ).fetchall()
    return {row["discord_id"]: row["total"] for row in rows}
