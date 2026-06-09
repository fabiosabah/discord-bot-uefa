# -*- coding: utf-8 -*-
from datetime import datetime

from core.db.connection import get_connection
from core.db.season_repo import get_current_season

FREE_MATCH_LIMIT = 2


def add_free_player(discord_id: int, display_name: str, season: int | None = None) -> bool:
    """Registra um jogador no trial gratuito. Retorna False se já estiver registrado."""
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM free_players WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO free_players (discord_id, display_name, season, matches_played, registered_at) VALUES (?, ?, ?, 0, ?)",
            (discord_id, display_name, season, datetime.utcnow().isoformat()),
        )
        conn.commit()
    return True


def is_free_player(discord_id: int, season: int | None = None) -> bool:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM free_players WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
    return row is not None


def get_free_player(discord_id: int, season: int | None = None) -> dict | None:
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT discord_id, display_name, season, matches_played, registered_at FROM free_players WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
    return dict(row) if row else None


def is_free_player_eligible(discord_id: int, season: int | None = None) -> bool:
    """Retorna True se o jogador está no trial e ainda tem jogos gratuitos disponíveis."""
    if season is None:
        season = get_current_season()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT matches_played FROM free_players WHERE discord_id = ? AND season = ?",
            (discord_id, season),
        ).fetchone()
    if row is None:
        return False
    return row["matches_played"] < FREE_MATCH_LIMIT


def increment_free_player_matches(discord_ids: list[int], season: int | None = None) -> None:
    """Incrementa matches_played para cada discord_id que seja um free player."""
    if not discord_ids:
        return
    if season is None:
        season = get_current_season()
    placeholders = ",".join("?" * len(discord_ids))
    with get_connection() as conn:
        conn.execute(
            f"UPDATE free_players SET matches_played = matches_played + 1 WHERE discord_id IN ({placeholders}) AND season = ?",
            discord_ids + [season],
        )
        conn.commit()


def clear_free_players_db(season: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM free_players WHERE season = ?", (season,))
        conn.commit()
    return cursor.rowcount
