# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime

from core.db.connection import get_connection

logger = logging.getLogger("Database")


def get_list_channel(guild_id: int) -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT list_channel_id FROM server_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["list_channel_id"] if row else None


def get_image_channel(guild_id: int) -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT image_channel_id FROM server_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["image_channel_id"] if row else None


def set_list_channel(guild_id: int, channel_id: int) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO server_config (guild_id, list_channel_id, image_channel_id)
            VALUES (?, ?, NULL)
            ON CONFLICT(guild_id) DO UPDATE SET list_channel_id = excluded.list_channel_id
        """, (guild_id, channel_id))
        conn.commit()
    logger.info(f"[DB] Canal de lista registrado para guild {guild_id}: {channel_id}")


def set_image_channel(guild_id: int, channel_id: int) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO server_config (guild_id, list_channel_id, image_channel_id)
            VALUES (?, NULL, ?)
            ON CONFLICT(guild_id) DO UPDATE SET image_channel_id = excluded.image_channel_id
        """, (guild_id, channel_id))
        conn.commit()
    logger.info(f"[DB] Canal de imagem registrado para guild {guild_id}: {channel_id}")


def clear_image_channel(guild_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO server_config (guild_id, list_channel_id, image_channel_id) VALUES (?, NULL, NULL)"
            " ON CONFLICT(guild_id) DO UPDATE SET image_channel_id = NULL",
            (guild_id,)
        )
        conn.commit()
    logger.info(f"[DB] Canal de imagem removido para guild {guild_id}")


def clear_list_channel(guild_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM server_config WHERE guild_id = ?", (guild_id,))
        conn.commit()
    logger.info(f"[DB] Canal de lista removido para guild {guild_id}")


def save_lobby_session(session, created_at: str | None = None) -> None:
    if not session.message or not session.message.guild:
        logger.warning("[DB] Não foi possível salvar sessão de lobby sem mensagem ou guild.")
        return

    if created_at is None:
        created_at = (
            session.created_at.isoformat()
            if hasattr(session, 'created_at') and session.created_at
            else datetime.now().isoformat()
        )

    player_ids = json.dumps(list(session.player_ids), ensure_ascii=False)
    waitlist_ids = json.dumps(list(session.waitlist_ids), ensure_ascii=False)
    auto_close_at = session.auto_close_at.isoformat() if session.auto_close_at else None
    full_at = session.full_at.isoformat() if getattr(session, 'full_at', None) else None

    all_join_times: dict[str, str] = {}
    if hasattr(session, 'player_join_times'):
        all_join_times.update({str(k): v.isoformat() for k, v in session.player_join_times.items()})
    if hasattr(session, 'waitlist_join_times'):
        all_join_times.update({str(k): v.isoformat() for k, v in session.waitlist_join_times.items()})
    join_times = json.dumps(all_join_times, ensure_ascii=False)

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO lobby_sessions
                (guild_id, session_id, message_id, channel_id, host_id, player_ids, waitlist_ids, closed, frozen, created_at, auto_close_at, join_times, full_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                session_id    = excluded.session_id,
                message_id    = excluded.message_id,
                channel_id    = excluded.channel_id,
                host_id       = excluded.host_id,
                player_ids    = excluded.player_ids,
                waitlist_ids  = excluded.waitlist_ids,
                closed        = excluded.closed,
                frozen        = excluded.frozen,
                created_at    = excluded.created_at,
                auto_close_at = excluded.auto_close_at,
                join_times    = excluded.join_times,
                full_at       = excluded.full_at
        """, (
            session.message.guild.id,
            session.id,
            session.message.id,
            session.message.channel.id,
            session.host.id,
            player_ids,
            waitlist_ids,
            1 if session.closed else 0,
            1 if getattr(session, 'frozen', False) else 0,
            created_at,
            auto_close_at,
            join_times,
            full_at,
        ))
        conn.commit()
    logger.info(f"[DB] Sessão de lobby salva para guild {session.message.guild.id} (msg {session.message.id}).")


def delete_lobby_session(guild_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM lobby_sessions WHERE guild_id = ?", (guild_id,))
        conn.commit()
    logger.info(f"[DB] Sessão de lobby removida para guild {guild_id}")


def get_lobby_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM lobby_sessions WHERE closed = 0").fetchall()
    return [
        {
            "guild_id":     row["guild_id"],
            "session_id":   row["session_id"],
            "message_id":   row["message_id"],
            "channel_id":   row["channel_id"],
            "host_id":      row["host_id"],
            "player_ids":   json.loads(row["player_ids"]),
            "waitlist_ids": json.loads(row["waitlist_ids"]),
            "closed":       bool(row["closed"]),
            "frozen":       bool(row["frozen"]) if "frozen" in row.keys() else False,
            "created_at":   row["created_at"],
            "auto_close_at": row["auto_close_at"] if "auto_close_at" in row.keys() else None,
            "join_times":   row["join_times"] if "join_times" in row.keys() else None,
            "full_at":      row["full_at"] if "full_at" in row.keys() else None,
        }
        for row in rows
    ]
