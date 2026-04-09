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
        # Audit log agora armazena IDs afetados para permitir UNDO
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
        conn.commit()
    logger.info("[DB] Banco de dados inicializado.")

def migrate_db():
    with get_connection() as conn:
        columns = conn.execute("PRAGMA table_info(audit_log)").fetchall()
        column_names = [col["name"] for col in columns]

        if "affected_ids" not in column_names:
            conn.execute("ALTER TABLE audit_log ADD COLUMN affected_ids TEXT")
            logger.info("[DB] Coluna 'affected_ids' adicionada via migration.")

        conn.commit()


# ─────────────────────────────────────────────
# Operações de jogadores
# ─────────────────────────────────────────────

def upsert_player(discord_id: int, display_name: str, wins: int, losses: int):
    """Sobrescreve wins e losses de um jogador (cria se não existir)."""
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
    """Adiciona 1 vitória ao jogador (cria com 0/0 se não existir)."""
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


def remove_win(discord_id: int):
    """Remove 1 vitória do jogador (para UNDO)."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            UPDATE players SET 
                wins = MAX(0, wins - 1),
                updated_at = ?
            WHERE discord_id = ?
        """, (now, discord_id))
        conn.commit()


def add_loss(discord_id: int, display_name: str):
    """Adiciona 1 derrota ao jogador (cria com 0/0 se não existir)."""
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


def remove_loss(discord_id: int):
    """Remove 1 derrota do jogador (para UNDO)."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute("""
            UPDATE players SET 
                losses = MAX(0, losses - 1),
                updated_at = ?
            WHERE discord_id = ?
        """, (now, discord_id))
        conn.commit()


def get_player(discord_id: int):
    """Retorna dados de um jogador ou None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        ).fetchone()
    return dict(row) if row else None


def get_ranking() -> list[dict]:
    """
    Retorna todos os jogadores ordenados por:
    1. pontos (wins*3 - losses) DESC
    2. wins DESC (desempate)
    """
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
    """
    Busca os 2 melhores jogadores (capitães) dentre uma lista específica de IDs.
    Critério: Pontos (W*3 - L) DESC, depois Vitórias DESC.
    """
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


# ─────────────────────────────────────────────
# Audit log & Undo
# ─────────────────────────────────────────────

def log_action(admin_id: int, admin_name: str, command: str, details: str, affected_ids: list[int] = None):
    affected_json = json.dumps(affected_ids) if affected_ids else None
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO audit_log (admin_id, admin_name, command, details, affected_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (admin_id, admin_name, command, details, affected_json, datetime.now().isoformat()))
        conn.commit()
    logger.info(f"[AUDIT] {admin_name} usou '{command}': {details}")


def get_last_admin_action(admin_id: int):
    """Busca a última ação reversível de um administrador."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM audit_log 
            WHERE admin_id = ? AND command IN ('!venceu', '!perdeu')
            ORDER BY id DESC LIMIT 1
        """, (admin_id,)).fetchone()
    return dict(row) if row else None


def delete_audit_log_entry(entry_id: int):
    """Remove uma entrada do log após o UNDO."""
    with get_connection() as conn:
        conn.execute("DELETE FROM audit_log WHERE id = ?", (entry_id,))
        conn.commit()
