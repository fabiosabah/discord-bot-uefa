# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from core.db.connection import get_connection

logger = logging.getLogger("Database")


def upsert_player(discord_id: int, display_name: str, wins: int, losses: int) -> None:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO players (discord_id, display_name, wins, losses, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                wins         = excluded.wins,
                losses       = excluded.losses,
                updated_at   = excluded.updated_at
        """, (discord_id, display_name, wins, losses, now))
        conn.commit()
    logger.info(f"[DB] Upsert: {display_name} ({discord_id}) → W:{wins} L:{losses}")


def get_player(discord_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        ).fetchone()
    return dict(row) if row else None


def find_player_by_display_name(display_name: str) -> list[dict]:
    if not display_name:
        return []
    search_term = display_name.strip()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT discord_id, display_name FROM players WHERE LOWER(display_name) = LOWER(?) LIMIT 10",
            (search_term,)
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        rows = conn.execute(
            "SELECT discord_id, display_name FROM players WHERE LOWER(display_name) LIKE LOWER(?) LIMIT 10",
            (f"%{search_term}%",)
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_player_names_exact(player_names: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    if not player_names:
        return mapping
    with get_connection() as conn:
        for name in player_names:
            if not name:
                continue
            normalized = " ".join(name.strip().replace("`", "").split())
            row = conn.execute(
                """
                SELECT discord_id FROM players WHERE LOWER(display_name) = LOWER(?)
                UNION
                SELECT discord_id FROM player_aliases WHERE LOWER(alias) = LOWER(?)
                LIMIT 1
                """,
                (normalized, normalized)
            ).fetchone()
            if row:
                mapping[name] = row["discord_id"]
    return mapping


def get_captains_from_list(player_ids: list[int]) -> list[dict]:
    if not player_ids:
        return []
    placeholders = ', '.join(['?'] * len(player_ids))
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT
                mp.discord_id,
                COALESCE(p.display_name, CAST(mp.discord_id AS TEXT)) AS display_name,
                SUM(CASE WHEN mp.team = m.winner_team THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN mp.team != m.winner_team THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN mp.team = m.winner_team THEN 3 ELSE -1 END) AS points
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.discord_id IN ({placeholders})
            GROUP BY mp.discord_id
            ORDER BY points DESC, wins DESC
            LIMIT 2
        """, player_ids).fetchall()
    return [dict(r) for r in rows]


def add_player_alias(discord_id: int, alias: str) -> None:
    alias = " ".join(alias.strip().replace("`", "").split())
    if not alias:
        return
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT discord_id FROM player_aliases WHERE LOWER(alias) = LOWER(?) AND discord_id != ?",
            (alias, discord_id)
        ).fetchall()
        if len(existing) >= 2:
            owners = [str(r["discord_id"]) for r in existing]
            raise ValueError(
                f"O nick '{alias}' já está associado a 2 usuários ({', '.join(owners)}). "
                f"Limite máximo atingido."
            )
        conn.execute(
            "INSERT OR IGNORE INTO player_aliases (discord_id, alias) VALUES (?, ?)",
            (discord_id, alias)
        )
        conn.commit()


def remove_player_alias(discord_id: int, alias: str) -> None:
    alias = alias.strip()
    if not alias:
        return
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM player_aliases WHERE discord_id = ? AND alias = ?",
            (discord_id, alias)
        )
        conn.commit()


def get_player_aliases(discord_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT alias FROM player_aliases WHERE discord_id = ? ORDER BY alias",
            (discord_id,)
        ).fetchall()
    return [row["alias"] for row in rows]


def get_all_player_aliases() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT pa.alias, pa.discord_id, COALESCE(p.display_name, '') AS display_name
            FROM player_aliases pa
            LEFT JOIN players p ON p.discord_id = pa.discord_id
            ORDER BY p.display_name, pa.alias
        """).fetchall()
    return [dict(r) for r in rows]


def _get_player_alias_names(discord_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT display_name AS name FROM players WHERE discord_id = ?
            UNION
            SELECT alias AS name FROM player_aliases WHERE discord_id = ?
            """,
            (discord_id, discord_id)
        ).fetchall()
    return [row["name"] for row in rows if row["name"]]
