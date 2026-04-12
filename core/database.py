# -*- coding: utf-8 -*-
import sqlite3
import logging
import os
import json
from datetime import datetime
from core.config import DB_PATH

logger = logging.getLogger("Database")

# ─────────────────────────────────────────────
# Inicialização do banco
# ─────────────────────────────────────────────

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Cria as tabelas se não existirem."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                discord_id   INTEGER PRIMARY KEY,
                display_name TEXT    NOT NULL,
                wins         INTEGER NOT NULL DEFAULT 0,
                losses       INTEGER NOT NULL DEFAULT 0,
                updated_at   TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id     INTEGER NOT NULL,
                admin_name   TEXT    NOT NULL,
                command      TEXT    NOT NULL,
                details      TEXT    NOT NULL,
                affected_ids TEXT,    -- JSON list of discord_ids
                created_at   TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id   INTEGER NOT NULL,
                match_id   INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                result     TEXT    NOT NULL,
                details    TEXT,
                created_at TEXT    NOT NULL,
                UNIQUE(audit_id, discord_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS server_config (
                guild_id        INTEGER PRIMARY KEY,
                list_channel_id INTEGER
            )
        """)
        conn.commit()
    logger.info("[DB] Banco de dados inicializado.")

def migrate_db():
    logger.info("[DB] Iniciando migração do banco de dados.")
    with get_connection() as conn:
        columns = conn.execute("PRAGMA table_info(audit_log)").fetchall()
        column_names = [col["name"] for col in columns]

        if "affected_ids" not in column_names:
            conn.execute("ALTER TABLE audit_log ADD COLUMN affected_ids TEXT")
            logger.info("[DB] Coluna 'affected_ids' adicionada via migration.")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id   INTEGER NOT NULL,
                match_id   INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                result     TEXT    NOT NULL,
                details    TEXT,
                created_at TEXT    NOT NULL,
                UNIQUE(audit_id, discord_id)
            )
        """)
        logger.info("[DB] Tabela 'match_history' criada ou já existente.")

        mh_columns = conn.execute("PRAGMA table_info(match_history)").fetchall()
        mh_column_names = [col["name"] for col in mh_columns]
        match_id_missing = "match_id" not in mh_column_names
        if match_id_missing:
            conn.execute("ALTER TABLE match_history ADD COLUMN match_id INTEGER")
            logger.info("[DB] Coluna 'match_id' adicionada via migration ao match_history.")

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_history_discord_id
            ON match_history(discord_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_history_created_at
            ON match_history(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_history_match_id
            ON match_history(match_id)
        """)
        logger.info("[DB] Índices de match_history criados ou já existentes.")

        existing_matches = conn.execute("SELECT COUNT(1) FROM match_history").fetchone()[0]
        if existing_matches == 0:
            rows = conn.execute("""
                SELECT id, command, affected_ids, details, created_at
                FROM audit_log
                WHERE command IN ('!venceu', '!perdeu')
                ORDER BY id ASC
            """).fetchall()
            inserted = 0
            for match_id, row in enumerate(rows, start=1):
                affected_ids = json.loads(row["affected_ids"]) if row["affected_ids"] else []
                result = "win" if row["command"] == "!venceu" else "loss"
                for discord_id in affected_ids:
                    conn.execute("""
                        INSERT OR IGNORE INTO match_history
                        (audit_id, match_id, discord_id, result, details, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (row["id"], match_id, discord_id, result, row["details"], row["created_at"]))
                    inserted += 1
            logger.info(f"[DB] Migração de histórico de partidas concluída: {len(rows)} eventos, {inserted} registros inseridos.")
        elif match_id_missing:
            audit_ids = conn.execute("SELECT DISTINCT audit_id FROM match_history ORDER BY audit_id ASC").fetchall()
            for match_id, audit_row in enumerate(audit_ids, start=1):
                conn.execute("UPDATE match_history SET match_id = ? WHERE audit_id = ?", (match_id, audit_row["audit_id"]))
            logger.info("[DB] match_id preenchido para histórico de partidas existente.")

        conn.commit()
    logger.info("[DB] Migração finalizada.")

def upsert_player(discord_id: int, display_name: str, wins: int, losses: int):
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


def add_win(discord_id: int, display_name: str):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO players (discord_id, display_name, wins, losses, updated_at)
            VALUES (?, ?, 1, 0, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                wins         = wins + 1,
                updated_at   = excluded.updated_at
        """, (discord_id, display_name, now))
        conn.commit()
    logger.info(f"[DB] Adicionada vitória para {display_name} ({discord_id}).")


def remove_win(discord_id: int):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            UPDATE players SET 
                wins = MAX(0, wins - 1),
                updated_at = ?
            WHERE discord_id = ?
        """, (now, discord_id))
        conn.commit()
    logger.info(f"[DB] Removida vitória para jogador {discord_id}.")


def add_loss(discord_id: int, display_name: str):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO players (discord_id, display_name, wins, losses, updated_at)
            VALUES (?, ?, 0, 1, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                losses       = losses + 1,
                updated_at   = excluded.updated_at
        """, (discord_id, display_name, now))
        conn.commit()
    logger.info(f"[DB] Adicionada derrota para {display_name} ({discord_id}).")


def remove_loss(discord_id: int):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            UPDATE players SET 
                losses = MAX(0, losses - 1),
                updated_at = ?
            WHERE discord_id = ?
        """, (now, discord_id))
        conn.commit()
    logger.info(f"[DB] Removida derrota para jogador {discord_id}.")


def get_player(discord_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        ).fetchone()
    return dict(row) if row else None


def get_list_channel(guild_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT list_channel_id FROM server_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["list_channel_id"] if row else None


def set_list_channel(guild_id: int, channel_id: int):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO server_config (guild_id, list_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET list_channel_id = excluded.list_channel_id
        """, (guild_id, channel_id))
        conn.commit()
    logger.info(f"[DB] Canal de lista registrado para guild {guild_id}: {channel_id}")


def clear_list_channel(guild_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM server_config WHERE guild_id = ?", (guild_id,))
        conn.commit()
    logger.info(f"[DB] Canal de lista removido para guild {guild_id}")


def delete_player(discord_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM players WHERE discord_id = ?", (discord_id,))
        conn.commit()
    logger.info(f"[DB] Jogador removido: {discord_id}")


def get_ranking() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                discord_id,
                display_name,
                wins,
                losses,
                (wins * 3 - losses) AS points,
                (wins + losses)      AS games
            FROM players
            ORDER BY points DESC, wins DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_captains_from_list(player_ids: list[int]) -> list[dict]:
    if not player_ids:
        return []
    
    placeholders = ', '.join(['?'] * len(player_ids))
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT
                discord_id,
                display_name,
                wins,
                losses,
                (wins * 3 - losses) AS points
            FROM players
            WHERE discord_id IN ({placeholders})
            ORDER BY points DESC, wins DESC
            LIMIT 2
        """, player_ids).fetchall()
    return [dict(r) for r in rows]


def log_action(admin_id: int, admin_name: str, command: str, details: str, affected_ids: list[int] = None, created_at: str | None = None) -> int:
    affected_json = json.dumps(affected_ids) if affected_ids else None
    created_at = created_at or datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO audit_log (admin_id, admin_name, command, details, affected_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (admin_id, admin_name, command, details, affected_json, created_at))
        conn.commit()
        audit_id = cursor.lastrowid
    logger.info(f"[AUDIT] {admin_name} usou '{command}': {details}")
    return audit_id


def get_next_match_id() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(match_id) AS max_match_id FROM match_history").fetchone()
    return (row["max_match_id"] or 0) + 1


def get_pending_match_id_for_opposite_result(result: str) -> int | None:
    opposite = "loss" if result == "win" else "win"
    with get_connection() as conn:
        row = conn.execute("""
            SELECT match_id,
                   MIN(result) AS only_result,
                   COUNT(DISTINCT result) AS result_count
            FROM match_history
            GROUP BY match_id
            ORDER BY match_id DESC
            LIMIT 1
        """).fetchone()

        if row and row["result_count"] == 1 and row["only_result"] == opposite:
            return row["match_id"]
    return None


def record_match_history(audit_id: int, affected_ids: list[int], result: str, details: str, created_at: str, match_id: int):
    if not affected_ids:
        logger.info(f"[DB] Sem IDs afetados para gravar match_history do audit {audit_id}.")
        return

    inserted = 0
    with get_connection() as conn:
        for discord_id in affected_ids:
            conn.execute("""
                INSERT OR IGNORE INTO match_history
                (audit_id, match_id, discord_id, result, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (audit_id, match_id, discord_id, result, details, created_at))
            inserted += 1
        conn.commit()
    logger.info(f"[DB] Gravado match_history do audit {audit_id} com match_id {match_id}: {inserted} registros.")


def log_match_action(admin_id: int, admin_name: str, command: str, details: str, affected_ids: list[int] = None) -> int:
    created_at = datetime.now().isoformat()
    audit_id = log_action(admin_id, admin_name, command, details, affected_ids, created_at=created_at)
    result = "win" if command == "!venceu" else "loss"
    pending_match_id = get_pending_match_id_for_opposite_result(result)
    match_id = pending_match_id if pending_match_id is not None else get_next_match_id()
    if pending_match_id is not None:
        logger.info(f"[DB] Agrupando comando {command} ao match existente {match_id}.")
    record_match_history(audit_id, affected_ids or [], result, details, created_at, match_id)
    return audit_id


def get_last_admin_action(admin_id: int):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM audit_log 
            WHERE admin_id = ? AND command IN ('!venceu', '!perdeu')
            ORDER BY id DESC LIMIT 1
        """, (admin_id,)).fetchone()
    return dict(row) if row else None


def delete_audit_log_entry(entry_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM match_history WHERE audit_id = ?", (entry_id,))
        conn.execute("DELETE FROM audit_log WHERE id = ?", (entry_id,))
        conn.commit()
    logger.info(f"[DB] Ação de auditoria {entry_id} desfeita e histórico de partidas correspondente removido.")


def create_or_replace_manual_match(match_id: int, winner_ids: list[int], loser_ids: list[int], admin_id: int, admin_name: str) -> int:
    details = f"Manual match {match_id}: winners={winner_ids} losers={loser_ids}"
    audit_id = log_action(admin_id, admin_name, "!registrarmatch", details, affected_ids=winner_ids + loser_ids)
    created_at = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute("DELETE FROM match_history WHERE match_id = ?", (match_id,))
        for discord_id in winner_ids:
            conn.execute("""
                INSERT INTO match_history
                (audit_id, match_id, discord_id, result, details, created_at)
                VALUES (?, ?, ?, 'win', ?, ?)
            """, (audit_id, match_id, discord_id, details, created_at))
        for discord_id in loser_ids:
            conn.execute("""
                INSERT INTO match_history
                (audit_id, match_id, discord_id, result, details, created_at)
                VALUES (?, ?, ?, 'loss', ?, ?)
            """, (audit_id, match_id, discord_id, details, created_at))
        conn.commit()

    logger.info(f"[DB] Match manual {match_id} registrado: {len(winner_ids)} vencedores, {len(loser_ids)} derrotados.")
    return audit_id


def get_raw_match_audit_events(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                al.id AS audit_id,
                al.command,
                al.details,
                al.affected_ids,
                al.created_at,
                GROUP_CONCAT(COALESCE(p.display_name, mh.discord_id)) AS players,
                MAX(mh.match_id) AS match_id,
                MIN(mh.result) AS result
            FROM audit_log al
            LEFT JOIN match_history mh ON mh.audit_id = al.id
            LEFT JOIN players p ON p.discord_id = mh.discord_id
            WHERE al.command IN ('!venceu', '!perdeu', '!registrarmatch')
            GROUP BY al.id
            ORDER BY al.id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return [
        {
            "audit_id": row["audit_id"],
            "command": row["command"],
            "details": row["details"],
            "affected_ids": json.loads(row["affected_ids"]) if row["affected_ids"] else [],
            "created_at": row["created_at"],
            "players": row["players"].split(',') if row["players"] else [],
            "match_id": row["match_id"],
            "result": row["result"],
        }
        for row in rows
    ]


def get_last_update():
    with get_connection() as conn:
        row = conn.execute("""
            SELECT created_at
            FROM audit_log
            WHERE command IN ('!venceu', '!perdeu')
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
    
    return row["created_at"] if row else None


def resolve_player_names(discord_ids: list[int]) -> dict:
    if not discord_ids:
        return {}

    placeholders = ', '.join(['?'] * len(discord_ids))
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT discord_id, display_name
            FROM players
            WHERE discord_id IN ({placeholders})
        """, discord_ids).fetchall()
    return {row['discord_id']: row['display_name'] for row in rows}


def get_match_summary(match_id: int) -> dict | None:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT mh.result,
                   mh.discord_id,
                   p.display_name,
                   al.command,
                   al.details,
                   al.created_at
            FROM match_history mh
            LEFT JOIN players p ON p.discord_id = mh.discord_id
            LEFT JOIN audit_log al ON al.id = mh.audit_id
            WHERE mh.match_id = ?
            ORDER BY mh.result DESC, p.display_name, mh.discord_id
        """, (match_id,)).fetchall()

    if not rows:
        return None

    winners = []
    losers = []
    for row in rows:
        display_name = row['display_name'] or str(row['discord_id'])
        if row['result'] == 'win':
            winners.append(display_name)
        else:
            losers.append(display_name)

    return {
        'match_id': match_id,
        'command': rows[0]['command'],
        'details': rows[0]['details'],
        'created_at': rows[0]['created_at'],
        'winners': winners,
        'losers': losers,
    }


def get_recent_match_ids(limit: int = 10) -> list[int]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT match_id
            FROM match_history
            ORDER BY match_id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [row['match_id'] for row in rows]


def get_recent_match_summaries(limit: int = 10) -> list[dict]:
    return [get_match_summary(match_id) for match_id in get_recent_match_ids(limit)]


def get_player_top_opponents(discord_id: int, result: str, limit: int = 3) -> list[dict]:
    query = """
        SELECT
            opp.discord_id,
            COALESCE(p.display_name, opp.discord_id) AS display_name,
            COUNT(DISTINCT opp.match_id) AS matches
        FROM match_history player
        JOIN match_history opp
            ON opp.match_id = player.match_id
            AND opp.discord_id != player.discord_id
            AND opp.result != player.result
        LEFT JOIN players p ON p.discord_id = opp.discord_id
        WHERE player.discord_id = ?
          AND player.result = ?
        GROUP BY opp.discord_id
        ORDER BY matches DESC, display_name ASC
        LIMIT ?
    """
    params = (discord_id, result, limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "discord_id": row["discord_id"],
            "display_name": row["display_name"],
            "count": row["matches"]
        }
        for row in rows
    ]


def get_player_match_history(discord_id: int, limit: int = 20) -> list[dict]:
    """Retorna os últimos eventos de partida de um jogador, do mais recente para o mais antigo."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT match_id, result, details, created_at
            FROM match_history
            WHERE discord_id = ?
            ORDER BY match_id DESC
            LIMIT ?
        """, (discord_id, limit)).fetchall()

    return [
        {
            "match_id": row["match_id"],
            "result": row["result"],
            "details": row["details"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_player_streak(discord_id: int, max_events: int = 50) -> dict:
    """Calcula a sequência atual de vitórias ou derrotas com base nos eventos de partida."""
    history = get_player_match_history(discord_id, max_events)
    if not history:
        return {"streak_type": None, "streak_count": 0, "recent": []}

    streak_type = history[0]["result"]
    streak_count = 0
    for event in history:
        if event["result"] != streak_type:
            break
        streak_count += 1

    return {
        "streak_type": streak_type,
        "streak_count": streak_count,
        "recent": history[:5],
    }


def delete_match_history_by_audit_id(audit_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM match_history WHERE audit_id = ?", (audit_id,))
        conn.commit()
