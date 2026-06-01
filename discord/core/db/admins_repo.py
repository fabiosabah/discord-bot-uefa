# -*- coding: utf-8 -*-
from datetime import datetime

from core.db.connection import get_connection


def add_admin_db(discord_id: int, display_name: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_admins (discord_id, display_name, added_at) VALUES (?, ?, ?)",
            (discord_id, display_name, datetime.utcnow().isoformat()),
        )
        conn.commit()


def remove_admin_db(discord_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM bot_admins WHERE discord_id = ?", (discord_id,))
        conn.commit()
    return cursor.rowcount > 0


def is_admin_db(discord_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM bot_admins WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
    return row is not None


def list_admins_db() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT discord_id, display_name, added_at FROM bot_admins ORDER BY added_at",
        ).fetchall()
    return [dict(r) for r in rows]


def clear_admins_db() -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM bot_admins")
        conn.commit()
    return cursor.rowcount
