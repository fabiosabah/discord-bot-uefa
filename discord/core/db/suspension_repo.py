# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from core.db.connection import get_connection


def add_suspension(discord_id: int, display_name: str, reason: str, days: int | None = None, applied_by: int | None = None) -> None:
    suspended_until = None
    if days:
        suspended_until = (datetime.utcnow() + timedelta(days=days)).isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO suspensions
               (discord_id, display_name, reason, suspended_at, suspended_until, applied_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (discord_id, display_name, reason, datetime.utcnow().isoformat(), suspended_until, applied_by),
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
            """SELECT 1 FROM suspensions
               WHERE discord_id = ?
               AND (suspended_until IS NULL OR suspended_until > ?)""",
            (discord_id, datetime.utcnow().isoformat()),
        ).fetchone()
    return row is not None


def get_suspension(discord_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM suspensions WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return dict(row) if row else None


def list_suspensions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM suspensions
               WHERE suspended_until IS NULL OR suspended_until > ?
               ORDER BY suspended_at DESC""",
            (datetime.utcnow().isoformat(),),
        ).fetchall()
    return [dict(r) for r in rows]
