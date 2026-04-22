# -*- coding: utf-8 -*-
import logging
import os
import sqlite3

from core.config import DB_PATH
from core.dota_heroes import resolve_hero_name, HERO_NAMES

logger = logging.getLogger("Database")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sanitize_hero_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    resolved_hero, _, _ = resolve_hero_name(value)
    return resolved_hero


def _populate_heroes() -> None:
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO heroes (name) VALUES (?)",
            [(name,) for name in HERO_NAMES]
        )
        conn.commit()


def init_db() -> None:
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
            CREATE TABLE IF NOT EXISTS player_aliases (
                discord_id INTEGER NOT NULL,
                alias      TEXT    NOT NULL COLLATE NOCASE,
                PRIMARY KEY (discord_id, alias)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id     INTEGER NOT NULL,
                admin_name   TEXT    NOT NULL,
                command      TEXT    NOT NULL,
                details      TEXT    NOT NULL,
                affected_ids TEXT,
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
                hero       TEXT,
                kills      INTEGER,
                deaths     INTEGER,
                assists    INTEGER,
                created_at TEXT    NOT NULL,
                UNIQUE(audit_id, discord_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS server_config (
                guild_id          INTEGER PRIMARY KEY,
                list_channel_id   INTEGER,
                image_channel_id  INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lobby_sessions (
                guild_id       INTEGER PRIMARY KEY,
                session_id     INTEGER NOT NULL,
                message_id     INTEGER NOT NULL,
                channel_id     INTEGER NOT NULL,
                host_id        INTEGER NOT NULL,
                player_ids     TEXT    NOT NULL,
                waitlist_ids   TEXT    NOT NULL,
                closed         INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT    NOT NULL,
                auto_close_at  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_screenshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id   INTEGER NOT NULL,
                guild_id     INTEGER NOT NULL,
                channel_id   INTEGER NOT NULL,
                author_id    INTEGER NOT NULL,
                image_url    TEXT    NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'pending',
                metadata     TEXT,
                created_at   TEXT    NOT NULL,
                processed_at TEXT
            )
        """)
        ms_columns = conn.execute("PRAGMA table_info(match_screenshots)").fetchall()
        ms_column_names = [col["name"] for col in ms_columns]
        if "image_data" not in ms_column_names:
            conn.execute("ALTER TABLE match_screenshots ADD COLUMN image_data BLOB")
            logger.info("[DB] Coluna 'image_data' adicionada via migration ao match_screenshots.")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_imports (
                match_id       INTEGER PRIMARY KEY,
                steam_match_id TEXT,
                dota_match_id  TEXT,
                match_date     TEXT,
                mode           TEXT,
                winner         TEXT,
                duration       TEXT,
                radiant_score  INTEGER,
                dire_score     INTEGER,
                raw_metadata   TEXT,
                created_at     TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                league_match_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                match_hash        TEXT    UNIQUE NOT NULL,
                external_match_id TEXT,
                winner_team       TEXT,
                duration          TEXT,
                match_datetime    TEXT,
                score_radiant     INTEGER,
                score_dire        INTEGER,
                created_at        TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_players (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                league_match_id  INTEGER NOT NULL,
                slot             INTEGER,
                player_name      TEXT    NOT NULL,
                discord_id       INTEGER,
                hero_name        TEXT,
                networth         INTEGER,
                team             TEXT,
                FOREIGN KEY (league_match_id) REFERENCES matches(league_match_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heroes (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_match_hash ON matches(match_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_created_at ON matches(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_league_match_id ON match_players(league_match_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_discord_id ON match_players(discord_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_hero_name ON match_players(hero_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_discord_hero ON match_players(discord_id, hero_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_admin_cmd_date ON audit_log(admin_id, command, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_history_audit_id ON match_history(audit_id)")
        conn.commit()
    _populate_heroes()
    logger.info("[DB] Banco de dados inicializado.")


def migrate_db() -> None:
    logger.info("[DB] Iniciando migração do banco de dados.")
    with get_connection() as conn:
        columns = conn.execute("PRAGMA table_info(audit_log)").fetchall()
        column_names = [col["name"] for col in columns]

        if "affected_ids" not in column_names:
            conn.execute("ALTER TABLE audit_log ADD COLUMN affected_ids TEXT")
            logger.info("[DB] Coluna 'affected_ids' adicionada via migration.")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_aliases (
                discord_id INTEGER NOT NULL,
                alias      TEXT    NOT NULL COLLATE NOCASE,
                PRIMARY KEY (discord_id, alias)
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
                hero       TEXT,
                created_at TEXT    NOT NULL,
                UNIQUE(audit_id, discord_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                league_match_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                match_hash        TEXT    UNIQUE NOT NULL,
                external_match_id TEXT,
                winner_team       TEXT,
                duration          TEXT,
                match_datetime    TEXT,
                score_radiant     INTEGER,
                score_dire        INTEGER,
                created_at        TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_players (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                league_match_id  INTEGER NOT NULL,
                slot             INTEGER,
                player_name      TEXT    NOT NULL,
                discord_id       INTEGER,
                hero_name        TEXT,
                networth         INTEGER,
                team             TEXT,
                FOREIGN KEY (league_match_id) REFERENCES matches(league_match_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_match_hash ON matches(match_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_league_match_id ON match_players(league_match_id)")

        mp_columns = conn.execute("PRAGMA table_info(match_players)").fetchall()
        mp_column_names = [col["name"] for col in mp_columns]
        for col in ("kills", "deaths", "assists"):
            if col not in mp_column_names:
                conn.execute(f"ALTER TABLE match_players ADD COLUMN {col} INTEGER")
                logger.info(f"[DB] Coluna '{col}' adicionada via migration ao match_players.")
        if "discord_id" not in mp_column_names:
            conn.execute("ALTER TABLE match_players ADD COLUMN discord_id INTEGER")
            logger.info("[DB] Coluna 'discord_id' adicionada via migration ao match_players.")

        mh_columns = conn.execute("PRAGMA table_info(match_history)").fetchall()
        mh_column_names = [col["name"] for col in mh_columns]
        for col in ("match_id", "hero", "kills", "deaths", "assists"):
            if col not in mh_column_names:
                conn.execute(f"ALTER TABLE match_history ADD COLUMN {col} {'INTEGER' if col != 'hero' else 'TEXT'}")
                logger.info(f"[DB] Coluna '{col}' adicionada via migration ao match_history.")

        lobby_columns = conn.execute("PRAGMA table_info(lobby_sessions)").fetchall()
        lobby_column_names = [col["name"] for col in lobby_columns]
        if "session_id" not in lobby_column_names:
            conn.execute("ALTER TABLE lobby_sessions ADD COLUMN session_id INTEGER NOT NULL DEFAULT 0")
            logger.info("[DB] Coluna 'session_id' adicionada via migration ao lobby_sessions.")
        if "auto_close_at" not in lobby_column_names:
            conn.execute("ALTER TABLE lobby_sessions ADD COLUMN auto_close_at TEXT")
            logger.info("[DB] Coluna 'auto_close_at' adicionada via migration ao lobby_sessions.")

        sc_columns = conn.execute("PRAGMA table_info(server_config)").fetchall()
        if "image_channel_id" not in [c["name"] for c in sc_columns]:
            conn.execute("ALTER TABLE server_config ADD COLUMN image_channel_id INTEGER")
            logger.info("[DB] Coluna 'image_channel_id' adicionada via migration ao server_config.")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_history_discord_id ON match_history(discord_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_history_created_at ON match_history(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_history_match_id ON match_history(match_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_screenshots_status ON match_screenshots(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_imports_steam_match_id ON match_imports(steam_match_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_imports_match_date ON match_imports(match_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_matches_created_at ON matches(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_discord_id ON match_players(discord_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_hero_name ON match_players(hero_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_players_discord_hero ON match_players(discord_id, hero_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_admin_cmd_date ON audit_log(admin_id, command, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_history_audit_id ON match_history(audit_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heroes (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE
            )
        """)
        logger.info("[DB] Índices e tabela heroes criados ou já existentes.")
        conn.commit()
    _populate_heroes()
    logger.info("[DB] Migração finalizada.")
