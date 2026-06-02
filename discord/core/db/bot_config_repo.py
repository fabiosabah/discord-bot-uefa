# -*- coding: utf-8 -*-
from core.db.connection import get_connection

_KEY_LOBBY_INTEGRATION = "lobby_integration_enabled"
_KEY_COMPLETED_LOBBIES = "completed_lobbies_count"
_KEY_FINAL_PHASE = "final_phase_active"


def is_lobby_integration_enabled() -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM bot_config WHERE key = ?", (_KEY_LOBBY_INTEGRATION,)
        ).fetchone()
    return row["value"] == "1" if row else False


def set_lobby_integration_enabled(enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
            (_KEY_LOBBY_INTEGRATION, "1" if enabled else "0"),
        )
        conn.commit()


def is_final_phase_active() -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM bot_config WHERE key = ?", (_KEY_FINAL_PHASE,)
        ).fetchone()
    return row["value"] == "1" if row else False


def set_final_phase_active(active: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
            (_KEY_FINAL_PHASE, "1" if active else "0"),
        )
        conn.commit()


def get_completed_lobbies_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM bot_config WHERE key = ?", (_KEY_COMPLETED_LOBBIES,)
        ).fetchone()
    return int(row["value"]) if row else 0


def increment_completed_lobbies() -> int:
    count = get_completed_lobbies_count() + 1
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
            (_KEY_COMPLETED_LOBBIES, str(count)),
        )
        conn.commit()
    return count
