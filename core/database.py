# -*- coding: utf-8 -*-
import sqlite3
import logging
import os
import json
import re
from datetime import datetime
from typing import Any
from core.config import DB_PATH
from core.dota_heroes import resolve_hero_name, HERO_NAMES

logger = logging.getLogger("Database")

# ─────────────────────────────────────────────
# Inicialização do banco
# ─────────────────────────────────────────────

def get_connection():
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
    """Insere todos os heróis oficiais na tabela heroes (idempotente)."""
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO heroes (name) VALUES (?)",
            [(name,) for name in HERO_NAMES]
        )
        conn.commit()


def get_all_hero_stats_from_matches() -> list[dict]:
    """
    Retorna estatísticas de todos os heróis jogados no campeonato,
    ordenados por quantidade de picks DESC.
    Usa índice em match_players(hero_name) via JOIN com heroes.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                h.name                                                          AS hero,
                COUNT(mp.id)                                                    AS picks,
                SUM(CASE WHEN mp.team = m.winner_team THEN 1 ELSE 0 END)       AS wins
            FROM heroes h
            JOIN match_players mp ON mp.hero_name = h.name
            JOIN matches m        ON m.league_match_id = mp.league_match_id
            GROUP BY h.name
            ORDER BY picks DESC, wins * 1.0 / COUNT(mp.id) DESC
        """).fetchall()
    return [
        {
            "hero":    row["hero"],
            "picks":   row["picks"],
            "wins":    row["wins"],
            "winrate": row["wins"] * 100.0 / row["picks"] if row["picks"] else 0.0,
        }
        for row in rows
    ]


def get_hero_match_history(hero_name: str) -> list[dict]:
    """Retorna todas as partidas em que um herói foi jogado, com quem jogou e o resultado."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                mp.league_match_id,
                mp.player_name,
                mp.discord_id,
                COALESCE(p.display_name, mp.player_name) AS display_name,
                mp.team,
                mp.kills,
                mp.deaths,
                mp.assists,
                m.winner_team,
                m.created_at
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.hero_name = ?
            ORDER BY mp.league_match_id DESC
        """, (hero_name,)).fetchall()
    return [
        {
            "league_match_id": row["league_match_id"],
            "player_name":     row["player_name"],
            "display_name":    str(row["display_name"]),
            "team":            row["team"],
            "kills":           row["kills"],
            "deaths":          row["deaths"],
            "assists":         row["assists"],
            "result":          "win" if row["team"] == row["winner_team"] else "loss",
            "created_at":      row["created_at"],
        }
        for row in rows
    ]


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

        # Migration: add image_data column to match_screenshots
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
                league_match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_hash      TEXT    UNIQUE NOT NULL,
                external_match_id TEXT,
                winner_team     TEXT,
                duration        TEXT,
                match_datetime  TEXT,
                score_radiant   INTEGER,
                score_dire      INTEGER,
                created_at      TEXT    NOT NULL
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

def migrate_db():
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
                league_match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_hash      TEXT    UNIQUE NOT NULL,
                external_match_id TEXT,
                winner_team     TEXT,
                duration        TEXT,
                match_datetime  TEXT,
                score_radiant   INTEGER,
                score_dire      INTEGER,
                created_at      TEXT    NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_matches_match_hash ON matches(match_hash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_players_league_match_id ON match_players(league_match_id)
        """)

        mp_columns = conn.execute("PRAGMA table_info(match_players)").fetchall()
        mp_column_names = [col["name"] for col in mp_columns]
        if "kills" not in mp_column_names:
            conn.execute("ALTER TABLE match_players ADD COLUMN kills INTEGER")
            logger.info("[DB] Coluna 'kills' adicionada via migration ao match_players.")
        if "deaths" not in mp_column_names:
            conn.execute("ALTER TABLE match_players ADD COLUMN deaths INTEGER")
            logger.info("[DB] Coluna 'deaths' adicionada via migration ao match_players.")
        if "assists" not in mp_column_names:
            conn.execute("ALTER TABLE match_players ADD COLUMN assists INTEGER")
            logger.info("[DB] Coluna 'assists' adicionada via migration ao match_players.")

        if "discord_id" not in mp_column_names:
            conn.execute("ALTER TABLE match_players ADD COLUMN discord_id INTEGER")
            logger.info("[DB] Coluna 'discord_id' adicionada via migration ao match_players.")

        logger.info("[DB] Tabela 'match_history' criada ou já existente.")

        mh_columns = conn.execute("PRAGMA table_info(match_history)").fetchall()
        mh_column_names = [col["name"] for col in mh_columns]
        if "match_id" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN match_id INTEGER")
            logger.info("[DB] Coluna 'match_id' adicionada via migration ao match_history.")

        if "hero" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN hero TEXT")
            logger.info("[DB] Coluna 'hero' adicionada via migration ao match_history.")
        if "kills" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN kills INTEGER")
            logger.info("[DB] Coluna 'kills' adicionada via migration ao match_history.")
        if "deaths" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN deaths INTEGER")
            logger.info("[DB] Coluna 'deaths' adicionada via migration ao match_history.")
        if "assists" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN assists INTEGER")
            logger.info("[DB] Coluna 'assists' adicionada via migration ao match_history.")

        lobby_columns = conn.execute("PRAGMA table_info(lobby_sessions)").fetchall()
        lobby_column_names = [col["name"] for col in lobby_columns]
        if "session_id" not in lobby_column_names:
            conn.execute("ALTER TABLE lobby_sessions ADD COLUMN session_id INTEGER NOT NULL DEFAULT 0")
            logger.info("[DB] Coluna 'session_id' adicionada via migration ao lobby_sessions.")

        if "auto_close_at" not in lobby_column_names:
            conn.execute("ALTER TABLE lobby_sessions ADD COLUMN auto_close_at TEXT")
            logger.info("[DB] Coluna 'auto_close_at' adicionada via migration ao lobby_sessions.")

        sc_columns = conn.execute("PRAGMA table_info(server_config)").fetchall()
        sc_column_names = [col["name"] for col in sc_columns]
        if "image_channel_id" not in sc_column_names:
            conn.execute("ALTER TABLE server_config ADD COLUMN image_channel_id INTEGER")
            logger.info("[DB] Coluna 'image_channel_id' adicionada via migration ao server_config.")

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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_screenshots_status
            ON match_screenshots(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_imports_steam_match_id
            ON match_imports(steam_match_id)
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_match_imports_match_date ON match_imports(match_date)")

        # Índices críticos para performance de queries de jogadores e heróis
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


def _get_player_display_name(discord_id: int) -> str:
    player = get_player(discord_id)
    if player and player.get("display_name"):
        return player["display_name"]
    return "Desconhecido"


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
    display_name = _get_player_display_name(discord_id)
    logger.info(f"[DB] Removida vitória para {display_name} ({discord_id}).")


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
    display_name = _get_player_display_name(discord_id)
    logger.info(f"[DB] Removida derrota para {display_name} ({discord_id}).")


def get_player(discord_id: int):
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
            normalized = name.strip()
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


def _resolve_ocr_player_discord_ids(players: list[dict[str, Any]], player_mapping: dict[str, dict[str, object]]) -> None:
    if not players:
        return

    lookup_names = []
    for player in players:
        if not isinstance(player, dict):
            continue

        player_name = (
            player.get("player_name")
            or player.get("name")
            or player.get("player")
            or ""
        ).strip()
        if not player_name:
            continue

        discord_id = player.get("discord_id")
        if discord_id is not None:
            continue

        mapping_entry = player_mapping.get(player_name)
        if mapping_entry is not None and mapping_entry.get("discord_id") is not None:
            player["discord_id"] = int(mapping_entry["discord_id"])
            continue

        lookup_names.append(player_name)

    resolved = resolve_player_names_exact(lookup_names)
    for player in players:
        if not isinstance(player, dict):
            continue

        player_name = (
            player.get("player_name")
            or player.get("name")
            or player.get("player")
            or ""
        ).strip()
        if not player_name or player.get("discord_id") is not None:
            continue

        discord_id = resolved.get(player_name)
        if discord_id is not None:
            player["discord_id"] = discord_id


def insert_ocr_match(job_id: int, player_mapping: dict[str, dict[str, object]], admin_id: int, admin_name: str) -> int:
    job = get_match_screenshot(job_id)
    if job is None:
        raise ValueError(f"Job de screenshot {job_id} não encontrado")

    if not job["metadata"]:
        raise ValueError("Job não contém metadados OCR para importação.")

    try:
        parsed = json.loads(job["metadata"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Metadados OCR inválidos: {exc}") from exc

    players = parsed.get("players_data") or parsed.get("players") or []
    if not isinstance(players, list) or len(players) < 2:
        raise ValueError("Metadados OCR não contêm lista válida de jogadores.")

    _resolve_ocr_player_discord_ids(players, player_mapping)

    from core.ocr import _normalize_team

    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    winner = parsed.get("winner") or match_info.get("winner_team") or match_info.get("winner")
    if winner is None:
        radiant_win = parsed.get("radiant_win")
        if radiant_win is None:
            raise ValueError("Não foi possível determinar o time vencedor nos metadados OCR.")
        winner = "Radiant" if radiant_win else "Dire"

    winner_team = _normalize_team(winner)
    if winner_team not in {"radiant", "dire"}:
        raise ValueError("Valor de vencedor desconhecido nos metadados OCR.")

    winners: list[int] = []
    losers: list[int] = []
    missing: list[str] = []

    for player in players:
        if not isinstance(player, dict):
            continue
        player_name = (player.get("player_name") or player.get("name") or player.get("player") or "").strip()
        team = _normalize_team(player.get("team") or player.get("side"))
        if not player_name or not team:
            continue

        discord_id = player.get("discord_id")
        if discord_id is None:
            entry = player_mapping.get(player_name)
            if entry is None:
                missing.append(player_name)
                continue
            discord_id = entry["discord_id"]
        else:
            try:
                discord_id = int(discord_id)
            except (TypeError, ValueError):
                missing.append(player_name)
                continue

        if team == winner_team:
            winners.append(discord_id)
        else:
            losers.append(discord_id)

    if missing:
        raise ValueError(f"Mapeamento incompleto. Falta mapear os jogadores: {', '.join(missing)}")

    if not winners or not losers:
        raise ValueError("A importação OCR não contém vencedores ou perdedores válidos.")

    from core.ocr import generate_match_hash

    match_hash = generate_match_hash(parsed)
    external_match_id = parsed.get("steam_match_id") or parsed.get("dota_match_id")
    league_match_id = insert_league_match(parsed, match_hash, external_match_id)

    details = f"OCR import job {job_id} steam_match_id={parsed.get('steam_match_id')} winner={winner} league_match_id={league_match_id}"
    audit_id = log_action(admin_id, admin_name, "!ocrmatch", details, affected_ids=winners + losers)
    match_id = get_next_match_id()
    created_at = datetime.now().isoformat()

    insert_match_import(
        match_id=match_id,
        steam_match_id=parsed.get("steam_match_id"),
        dota_match_id=parsed.get("dota_match_id"),
        match_date=parsed.get("match_date") or parsed.get("game_details", {}).get("date"),
        mode=parsed.get("mode") or parsed.get("game_details", {}).get("mode"),
        winner=winner.title(),
        duration=parsed.get("duration") or parsed.get("game_details", {}).get("duration"),
        radiant_score=parsed.get("radiant_score"),
        dire_score=parsed.get("dire_score"),
        raw_metadata=json.dumps(parsed, ensure_ascii=False),
        created_at=created_at
    )

    insert_match_history_from_ocr_import(audit_id, match_id, league_match_id, details, created_at)

    original_metadata = parsed
    original_metadata["imported_by"] = admin_id
    original_metadata["match_id"] = match_id
    original_metadata["league_match_id"] = league_match_id
    original_metadata["match_hash"] = match_hash
    original_metadata["mapping"] = player_mapping
    set_match_screenshot_status(job_id, "imported", metadata=json.dumps(original_metadata, ensure_ascii=False))
    logger.info(f"[DB] OCR match importado job {job_id} → match_id {match_id} league_match_id {league_match_id}")
    return league_match_id


def insert_match_import(
    match_id: int,
    steam_match_id: str | None,
    dota_match_id: str | None,
    match_date: str | None,
    mode: str | None,
    winner: str | None,
    duration: str | None,
    radiant_score: int | None,
    dire_score: int | None,
    raw_metadata: str | None,
    created_at: str
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO match_imports (match_id, steam_match_id, dota_match_id, match_date, mode, winner, duration, radiant_score, dire_score, raw_metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (match_id, steam_match_id, dota_match_id, match_date, mode, winner, duration, radiant_score, dire_score, raw_metadata, created_at)
        )
        conn.commit()


def insert_match_history_from_ocr_import(audit_id: int, match_id: int, league_match_id: int, details: str, created_at: str) -> int:
    with get_connection() as conn:
        match_row = conn.execute(
            "SELECT winner_team FROM matches WHERE league_match_id = ?",
            (league_match_id,)
        ).fetchone()
        if not match_row:
            return 0

        winner_team = match_row["winner_team"]
        inserted = 0

        player_rows = conn.execute(
            "SELECT discord_id, hero_name, kills, deaths, assists, team FROM match_players WHERE league_match_id = ?",
            (league_match_id,)
        ).fetchall()

        for player in player_rows:
            discord_id = player["discord_id"]
            if discord_id is None:
                continue

            team = player["team"]
            if team is None or winner_team is None:
                continue

            result = "win" if team == winner_team else "loss"
            conn.execute(
                "INSERT OR IGNORE INTO match_history (audit_id, match_id, discord_id, result, details, hero, kills, deaths, assists, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    audit_id,
                    match_id,
                    discord_id,
                    result,
                    details,
                    player["hero_name"],
                    player["kills"],
                    player["deaths"],
                    player["assists"],
                    created_at,
                )
            )
            inserted += 1
        conn.commit()
    logger.info(f"[DB] Gravado match_history OCR para league_match_id {league_match_id} com {inserted} registros.")
    return inserted


def insert_league_match(parsed: dict[str, Any], match_hash: str, external_match_id: str | None = None) -> int:
    from core.ocr import _normalize_team

    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    winner_team = _normalize_team((match_info.get("winner_team") or match_info.get("winner") or "").strip())
    duration = match_info.get("duration")
    match_datetime = match_info.get("datetime")
    score = match_info.get("score") or {}
    radiant_score = score.get("radiant")
    dire_score = score.get("dire")
    created_at = datetime.now().isoformat()

    players_data = parsed.get("players_data") or parsed.get("players") or []
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO matches (match_hash, external_match_id, winner_team, duration, match_datetime, score_radiant, score_dire, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (match_hash, external_match_id, winner_team, duration, match_datetime, radiant_score, dire_score, created_at)
        )
        if cursor.rowcount == 0:
            row = conn.execute(
                "SELECT league_match_id FROM matches WHERE match_hash = ?",
                (match_hash,)
            ).fetchone()
            if row:
                raise ValueError("Partida Duplicada")
            raise RuntimeError("Falha ao recuperar league_match_id para partida duplicada.")

        league_match_id = cursor.lastrowid

        def parse_int_value(value: Any) -> int | None:
            if value is None:
                return None
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                return None

        for index, player in enumerate(players_data, start=1):
            if not isinstance(player, dict):
                continue
            player_name = (player.get("player_name") or player.get("name") or player.get("player") or "").strip()
            hero_name = _sanitize_hero_name((player.get("hero_name") or player.get("hero") or "") if player.get("hero_name") or player.get("hero") else None)
            kills = deaths = assists = None

            if player.get("kills") is not None or player.get("deaths") is not None or player.get("assists") is not None:
                kills = parse_int_value(player.get("kills"))
                deaths = parse_int_value(player.get("deaths"))
                assists = parse_int_value(player.get("assists"))
            else:
                raw_kda = player.get("kda") or player.get("score")
                if isinstance(raw_kda, dict):
                    kills = parse_int_value(raw_kda.get("kills") or raw_kda.get("kill"))
                    deaths = parse_int_value(raw_kda.get("deaths") or raw_kda.get("death"))
                    assists = parse_int_value(raw_kda.get("assists") or raw_kda.get("assist"))
                else:
                    raw_kda_text = str(raw_kda).strip() if raw_kda is not None else ""
                    if "/" in raw_kda_text:
                        parts = raw_kda_text.split("/")
                        kills = parse_int_value(parts[0] if len(parts) > 0 else None)
                        deaths = parse_int_value(parts[1] if len(parts) > 1 else None)
                        assists = parse_int_value(parts[2] if len(parts) > 2 else None)
            networth = player.get("networth") or player.get("net_worth")
            try:
                networth = int(networth) if networth is not None and str(networth).strip() != "" else None
            except (TypeError, ValueError):
                networth = None
            discord_id = player.get("discord_id")
            if discord_id is not None:
                try:
                    discord_id = int(str(discord_id).strip())
                except (TypeError, ValueError):
                    discord_id = None
            team = _normalize_team(player.get("team") or player.get("side"))
            conn.execute(
                "INSERT INTO match_players (league_match_id, slot, player_name, discord_id, hero_name, kills, deaths, assists, networth, team) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (league_match_id, index, player_name, discord_id, hero_name, kills, deaths, assists, networth, team)
            )
        conn.commit()

    return league_match_id


def get_match_by_league_id(league_match_id: int) -> dict | None:
    with get_connection() as conn:
        match_row = conn.execute(
            "SELECT league_match_id, match_hash, external_match_id, winner_team, duration, match_datetime, score_radiant, score_dire, created_at FROM matches WHERE league_match_id = ?",
            (league_match_id,)
        ).fetchone()
        if not match_row:
            return None

        player_rows = conn.execute(
            "SELECT slot, player_name, discord_id, hero_name, kills, deaths, assists, networth, team FROM match_players WHERE league_match_id = ? ORDER BY slot",
            (league_match_id,)
        ).fetchall()

    return {
        "league_match_id": match_row["league_match_id"],
        "match_hash": match_row["match_hash"],
        "external_match_id": match_row["external_match_id"],
        "match_info": {
            "winner_team": match_row["winner_team"],
            "duration": match_row["duration"],
            "datetime": match_row["match_datetime"],
            "match_id": match_row["external_match_id"],
            "score": {
                "radiant": match_row["score_radiant"],
                "dire": match_row["score_dire"],
            },
        },
        "players_data": [
            {
                "slot": row["slot"],
                "player_name": row["player_name"],
                "discord_id": row["discord_id"],
                "hero_name": row["hero_name"],
                "kills": row["kills"],
                "deaths": row["deaths"],
                "assists": row["assists"],
                "networth": row["networth"],
                "team": row["team"],
            }
            for row in player_rows
        ],
        "created_at": match_row["created_at"],
    }


def get_list_channel(guild_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT list_channel_id FROM server_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["list_channel_id"] if row else None


def get_image_channel(guild_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT image_channel_id FROM server_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    return row["image_channel_id"] if row else None


def set_list_channel(guild_id: int, channel_id: int):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO server_config (guild_id, list_channel_id, image_channel_id)
            VALUES (?, ?, NULL)
            ON CONFLICT(guild_id) DO UPDATE SET list_channel_id = excluded.list_channel_id
        """, (guild_id, channel_id))
        conn.commit()
    logger.info(f"[DB] Canal de lista registrado para guild {guild_id}: {channel_id}")


def set_image_channel(guild_id: int, channel_id: int):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO server_config (guild_id, list_channel_id, image_channel_id)
            VALUES (?, NULL, ?)
            ON CONFLICT(guild_id) DO UPDATE SET image_channel_id = excluded.image_channel_id
        """, (guild_id, channel_id))
        conn.commit()
    logger.info(f"[DB] Canal de imagem registrado para guild {guild_id}: {channel_id}")


def clear_image_channel(guild_id: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO server_config (guild_id, list_channel_id, image_channel_id) VALUES (?, NULL, NULL)"
            " ON CONFLICT(guild_id) DO UPDATE SET image_channel_id = NULL",
            (guild_id,)
        )
        conn.commit()
    logger.info(f"[DB] Canal de imagem removido para guild {guild_id}")


def clear_list_channel(guild_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM server_config WHERE guild_id = ?", (guild_id,))
        conn.commit()
    logger.info(f"[DB] Canal de lista removido para guild {guild_id}")


def save_lobby_session(session, created_at: str | None = None):
    if not session.message or not session.message.guild:
        logger.warning("[DB] Não foi possível salvar sessão de lobby sem mensagem ou guild.")
        return

    if created_at is None:
        # Usa o created_at da sessão se disponível, caso contrário usa agora
        created_at = session.created_at.isoformat() if hasattr(session, 'created_at') and session.created_at else datetime.now().isoformat()

    player_ids = json.dumps(list(session.player_ids), ensure_ascii=False)
    waitlist_ids = json.dumps(list(session.waitlist_ids), ensure_ascii=False)
    auto_close_at = session.auto_close_at.isoformat() if session.auto_close_at else None

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO lobby_sessions
                (guild_id, session_id, message_id, channel_id, host_id, player_ids, waitlist_ids, closed, created_at, auto_close_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                session_id   = excluded.session_id,
                message_id   = excluded.message_id,
                channel_id   = excluded.channel_id,
                host_id      = excluded.host_id,
                player_ids   = excluded.player_ids,
                waitlist_ids = excluded.waitlist_ids,
                closed       = excluded.closed,
                created_at   = excluded.created_at,
                auto_close_at= excluded.auto_close_at
        """, (
            session.message.guild.id,
            session.id,
            session.message.id,
            session.message.channel.id,
            session.host.id,
            player_ids,
            waitlist_ids,
            1 if session.closed else 0,
            created_at,
            auto_close_at
        ))
        conn.commit()
    logger.info(f"[DB] Sessão de lobby salva para guild {session.message.guild.id} (msg {session.message.id}).")


def delete_lobby_session(guild_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM lobby_sessions WHERE guild_id = ?", (guild_id,))
        conn.commit()
    logger.info(f"[DB] Sessão de lobby removida para guild {guild_id}")


def get_lobby_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM lobby_sessions WHERE closed = 0").fetchall()
    return [
        {
            "guild_id": row["guild_id"],
            "session_id": row["session_id"],
            "message_id": row["message_id"],
            "channel_id": row["channel_id"],
            "host_id": row["host_id"],
            "player_ids": json.loads(row["player_ids"]),
            "waitlist_ids": json.loads(row["waitlist_ids"]),
            "closed": bool(row["closed"]),
            "created_at": row["created_at"],
            "auto_close_at": row["auto_close_at"] if "auto_close_at" in row.keys() else None
        }
        for row in rows
    ]


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


def get_ranking_from_matches() -> list[dict]:
    query = """
        SELECT DISTINCT mp.discord_id AS discord_id,
               COALESCE(p.display_name, mp.discord_id) AS display_name
        FROM match_players mp
        LEFT JOIN players p ON p.discord_id = mp.discord_id
        WHERE mp.discord_id IS NOT NULL
        UNION
        SELECT discord_id, display_name FROM players
    """
    
    logger.info(f"Executing get_ranking_from_matches query: {query.strip()}")
    
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()

    ranking = []
    for row in rows:
        discord_id = row["discord_id"]
        # COALESCE pode retornar mp.discord_id (int) quando o jogador não tem registro em players
        display_name = str(row["display_name"]) if row["display_name"] is not None else str(discord_id)
        stats = get_player_match_stats_from_matches(discord_id)
        if stats["matches"] == 0:
            continue

        ranking.append({
            "discord_id": discord_id,
            "display_name": display_name,
            "wins": stats["wins"],
            "losses": stats["losses"],
            "points": stats["wins"] * 3 - stats["losses"],
            "games": stats["matches"],
        })

    ranking.sort(key=lambda item: (-item["points"], -item["wins"], str(item["display_name"])))
    return ranking


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


def add_player_alias(discord_id: int, alias: str) -> None:
    alias = alias.strip()
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
        row = conn.execute(
            "SELECT MAX(match_id) AS max_match_id FROM ("
            "SELECT match_id FROM match_history UNION ALL SELECT match_id FROM match_imports)"
        ).fetchone()
    return (row["max_match_id"] or 0) + 1


def _find_open_match_id(result: str, same_side: bool = False) -> int | None:
    query = """
        SELECT match_id,
               SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS win_count,
               SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS loss_count
        FROM match_history
        GROUP BY match_id
        HAVING
    """

    if same_side:
        query += """
            (? = 'win' AND win_count > 0 AND loss_count = 0 AND win_count < 5)
            OR (? = 'loss' AND loss_count > 0 AND win_count = 0 AND loss_count < 5)
        """
    else:
        query += """
            (? = 'win' AND win_count = 0 AND loss_count > 0)
            OR (? = 'loss' AND loss_count = 0 AND win_count > 0)
        """

    query += "\n            ORDER BY match_id DESC\n            LIMIT 1"

    with get_connection() as conn:
        row = conn.execute(query, (result, result)).fetchone()
    return row["match_id"] if row else None


def get_pending_match_id_for_opposite_result(result: str) -> int | None:
    return _find_open_match_id(result, same_side=False)


def get_pending_match_id_for_same_side(result: str) -> int | None:
    return _find_open_match_id(result, same_side=True)


def record_match_history(audit_id: int, affected_ids: list[int], result: str, details: str, created_at: str, match_id: int, hero: str | None = None, kills: int | None = None, deaths: int | None = None, assists: int | None = None):
    if not affected_ids:
        logger.info(f"[DB] Sem IDs afetados para gravar match_history do audit {audit_id}.")
        return

    inserted = 0
    with get_connection() as conn:
        for discord_id in affected_ids:
            conn.execute("""
                INSERT OR IGNORE INTO match_history
                (audit_id, match_id, discord_id, result, details, hero, kills, deaths, assists, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (audit_id, match_id, discord_id, result, details, hero, kills, deaths, assists, created_at))
            inserted += 1
        conn.commit()
    logger.info(f"[DB] Gravado match_history do audit {audit_id} com match_id {match_id}: {inserted} registros.")


def update_match_hero(match_id: int, discord_id: int, hero: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE match_history SET hero = ? WHERE match_id = ? AND discord_id = ?",
            (hero, match_id, discord_id)
        )
        conn.commit()
    updated = cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if updated:
        logger.info(f"[DB] Match {match_id} hero atualizado para {discord_id}: {hero}")
    return updated > 0


def update_league_match_heroes(league_match_id: int, hero_names: list[str]) -> int:
    with get_connection() as conn:
        updated = 0
        for slot, hero_name in enumerate(hero_names, start=1):
            sanitized_hero = _sanitize_hero_name(hero_name) if hero_name is not None else None
            cursor = conn.execute(
                "UPDATE match_players SET hero_name = ? WHERE league_match_id = ? AND slot = ?",
                (sanitized_hero, league_match_id, slot)
            )
            updated += cursor.rowcount if hasattr(cursor, "rowcount") else 0
        conn.commit()
    if updated:
        logger.info(f"[DB] Atualizados {updated} heróis para league_match_id {league_match_id}.")
    return updated


def update_league_match_hero_by_slot(league_match_id: int, slot: int, hero_name: str) -> bool:
    sanitized_hero = _sanitize_hero_name(hero_name)
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE match_players SET hero_name = ? WHERE league_match_id = ? AND slot = ?",
            (sanitized_hero, league_match_id, slot)
        )
        conn.commit()
    updated = cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if updated:
        logger.info(f"[DB] Hero do slot {slot} atualizado para league_match_id {league_match_id}: {sanitized_hero}")
    return updated > 0


def update_league_match_player_names(league_match_id: int, player_names: list[str]) -> int:
    with get_connection() as conn:
        updated = 0
        for slot, player_name in enumerate(player_names, start=1):
            cursor = conn.execute(
                "UPDATE match_players SET player_name = ? WHERE league_match_id = ? AND slot = ?",
                (player_name.strip(), league_match_id, slot)
            )
            updated += cursor.rowcount if hasattr(cursor, "rowcount") else 0
        conn.commit()
    if updated:
        logger.info(f"[DB] Atualizados {updated} nomes de jogadores para league_match_id {league_match_id}.")
    return updated


def update_league_match_player_name_by_slot(league_match_id: int, slot: int, player_name: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE match_players SET player_name = ? WHERE league_match_id = ? AND slot = ?",
            (player_name.strip(), league_match_id, slot)
        )
        conn.commit()
    updated = cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if updated:
        logger.info(f"[DB] Atualizado nick do slot {slot} para league_match_id {league_match_id}.")
    return updated > 0

def update_league_match_duration(league_match_id: int, duration: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE matches SET duration = ? WHERE league_match_id = ?",
            (duration.strip(), league_match_id)
        )
        conn.commit()
    updated = cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if updated:
        logger.info(f"[DB] Duração da partida {league_match_id} atualizada para: {duration}")
    return updated > 0


def log_match_action(admin_id: int, admin_name: str, command: str, details: str, affected_ids: list[int] = None) -> int:
    created_at = datetime.now().isoformat()
    return log_action(admin_id, admin_name, command, details, affected_ids, created_at=created_at)


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
        row = conn.execute(
            "SELECT command, details, affected_ids FROM audit_log WHERE id = ?",
            (entry_id,)
        ).fetchone()
        conn.execute("DELETE FROM match_history WHERE audit_id = ?", (entry_id,))
        conn.execute("DELETE FROM audit_log WHERE id = ?", (entry_id,))
        conn.commit()

    if row:
        logger.info(
            f"[DB] Ação de auditoria {entry_id} desfeita: {row['command']} | {row['details']} | afetados: {row['affected_ids']}"
        )
    else:
        logger.info(f"[DB] Ação de auditoria {entry_id} desfeita e histórico de partidas correspondente removido.")


def delete_match_history() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM match_history")
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM match_players")
        conn.execute("DELETE FROM match_imports")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'match_history'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'matches'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'match_players'")
        conn.commit()
    logger.info("[DB] Histórico de partidas e partidas importadas apagados.")


def get_match_created_at(league_match_id: int) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT created_at FROM matches WHERE league_match_id = ? LIMIT 1",
            (league_match_id,)
        ).fetchone()
    return row["created_at"] if row else None


def count_match_deletions_today(admin_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM audit_log WHERE admin_id = ? AND command = '!apagarmatch' AND created_at >= ?",
            (admin_id, today)
        ).fetchone()
    return row["cnt"] if row else 0


def delete_league_match(league_match_id: int) -> bool:
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM matches WHERE league_match_id = ? LIMIT 1",
            (league_match_id,)
        ).fetchone()
        if not exists:
            return False

        conn.execute("DELETE FROM match_players WHERE league_match_id = ?", (league_match_id,))
        conn.execute("DELETE FROM matches WHERE league_match_id = ?", (league_match_id,))
        conn.commit()
    logger.info(f"[DB] Partida importada league_match_id {league_match_id} removida.")
    return True


def delete_match_screenshots() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM match_screenshots")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'match_screenshots'")
        conn.commit()
    logger.info("[DB] Histórico de screenshots apagado.")


def delete_match_screenshot(job_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM match_screenshots WHERE id = ?", (job_id,))
        conn.commit()
        row = conn.execute("SELECT COUNT(1) AS count FROM match_screenshots").fetchone()
        if row and row["count"] == 0:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'match_screenshots'")
            conn.commit()
            logger.info("[DB] sqlite_sequence reset for match_screenshots after deletion.")
    logger.info(f"[DB] Screenshot job {job_id} removido.")


def enqueue_match_screenshot(message_id: int, guild_id: int, channel_id: int, author_id: int, image_url: str, created_at: str) -> int:
    import requests
    
    logger.info(f"[DB] Starting to enqueue screenshot: message_id={message_id}, guild_id={guild_id}, channel_id={channel_id}, author_id={author_id}, image_url={image_url}")
    
    # Download the image
    try:
        logger.debug(f"[DB] Downloading image from {image_url}")
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        image_data = response.content
        image_size = len(image_data)
        logger.info(f"[DB] Successfully downloaded image: {image_size} bytes from {image_url}")
    except Exception as e:
        logger.error(f"[DB] Failed to download image from {image_url}: {e}")
        raise
    
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO match_screenshots (message_id, guild_id, channel_id, author_id, image_url, image_data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, author_id, image_url, image_data, created_at)
        )
        conn.commit()
        job_id = cursor.lastrowid
    logger.info(f"[DB] Screenshot enqueued successfully: job {job_id} ({image_url}, {image_size} bytes)")
    return job_id


def is_match_screenshot_enqueued(message_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM match_screenshots WHERE message_id = ? LIMIT 1",
            (message_id,)
        ).fetchone()
    return bool(row)


def get_pending_match_screenshots(limit: int = 20) -> list[dict]:
    logger.debug(f"[DB] Fetching up to {limit} pending screenshot jobs")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, message_id, guild_id, channel_id, author_id, image_url, image_data, status, metadata, created_at FROM match_screenshots WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,)
        ).fetchall()
    jobs = [dict(row) for row in rows]
    logger.debug(f"[DB] Found {len(jobs)} pending jobs")
    return jobs


def set_match_screenshot_status(job_id: int, status: str, metadata: str | None = None) -> None:
    logger.debug(f"[DB] Updating job {job_id} status to '{status}' with metadata length {len(metadata) if metadata else 0}")
    with get_connection() as conn:
        conn.execute(
            "UPDATE match_screenshots SET status = ?, metadata = ?, processed_at = ? WHERE id = ?",
            (status, metadata, datetime.now().isoformat(), job_id)
        )
        conn.commit()
    logger.info(f"[DB] Screenshot job {job_id} marked as {status}.")


def get_match_screenshot(job_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, message_id, guild_id, channel_id, author_id, image_url, image_data, status, metadata, created_at, processed_at FROM match_screenshots WHERE id = ?",
            (job_id,)
        ).fetchone()
    return dict(row) if row else None


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


def get_last_ocr_match_info() -> dict | None:
    """Retorna id e data da última partida importada via OCR."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT league_match_id, created_at
            FROM matches
            ORDER BY league_match_id DESC
            LIMIT 1
        """).fetchone()
    return dict(row) if row else None


def get_league_hero_winrates_from_matches(min_games: int = 2) -> list[dict]:
    """Retorna winrate por herói em todo o campeonato, filtrado por mínimo de partidas."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                mp.hero_name,
                COUNT(*) AS games,
                SUM(CASE WHEN mp.team = m.winner_team THEN 1 ELSE 0 END) AS wins
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            WHERE mp.hero_name IS NOT NULL AND mp.hero_name != ''
            GROUP BY mp.hero_name
            HAVING COUNT(*) >= ?
            ORDER BY wins * 1.0 / COUNT(*) DESC, games DESC
        """, (min_games,)).fetchall()
    return [
        {
            "hero":    row["hero_name"],
            "games":   row["games"],
            "wins":    row["wins"],
            "winrate": row["wins"] * 100.0 / row["games"],
        }
        for row in rows
    ]


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
                   mh.hero,
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
        hero = row['hero']
        if hero:
            display_name = f"{display_name} ({hero})"
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


def get_player_top_teammates(discord_id: int, limit: int = 3) -> list[dict]:
    query = """
        SELECT
            team.discord_id,
            COALESCE(p.display_name, team.discord_id) AS display_name,
            COUNT(DISTINCT team.match_id) AS matches
        FROM match_history player
        JOIN match_history team
            ON team.match_id = player.match_id
            AND team.discord_id != player.discord_id
            AND team.result = player.result
        LEFT JOIN players p ON p.discord_id = team.discord_id
        WHERE player.discord_id = ?
        GROUP BY team.discord_id
        ORDER BY matches DESC, display_name ASC
        LIMIT ?
    """
    params = (discord_id, limit)

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


def get_player_top_heroes(discord_id: int, limit: int = 5) -> list[dict]:
    query = """
        SELECT hero, COUNT(*) AS plays
        FROM match_history
        WHERE discord_id = ?
          AND hero IS NOT NULL
          AND hero != ''
        GROUP BY hero
        ORDER BY plays DESC, hero ASC
        LIMIT ?
    """
    params = (discord_id, limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "hero": row["hero"],
            "plays": row["plays"]
        }
        for row in rows
    ]


def _parse_kda_from_details(details: str) -> tuple[int | None, int | None, int | None]:
    if not isinstance(details, str):
        return None, None, None

    match = re.search(r"(\d+)\s*[/\\-]\s*(\d+)\s*[/\\-]\s*(\d+)", details)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    match = re.search(r"kda[:\s]*(\d+)\s*[/\\-]\s*(\d+)\s*[/\\-]\s*(\d+)", details, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    return None, None, None


def get_player_history_stats(discord_id: int) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT result, details, kills, deaths, assists FROM match_history WHERE discord_id = ?",
            (discord_id,)
        ).fetchall()

    total_matches = len(rows)
    wins = sum(1 for row in rows if row["result"] == "win")
    losses = sum(1 for row in rows if row["result"] == "loss")
    total_kills = total_deaths = total_assists = 0
    kda_rows = 0

    for row in rows:
        kills = row["kills"]
        deaths = row["deaths"]
        assists = row["assists"]
        if kills is not None and deaths is not None and assists is not None:
            total_kills += kills
            total_deaths += deaths
            total_assists += assists
            kda_rows += 1
        else:
            parsed_kills, parsed_deaths, parsed_assists = _parse_kda_from_details(row["details"] or "")
            if parsed_kills is not None and parsed_deaths is not None and parsed_assists is not None:
                total_kills += parsed_kills
                total_deaths += parsed_deaths
                total_assists += parsed_assists
                kda_rows += 1

    winrate = (wins / total_matches * 100) if total_matches else 0
    return {
        "matches": total_matches,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "total_kills": total_kills,
        "total_deaths": total_deaths,
        "total_assists": total_assists,
        "kda_rows": kda_rows,
    }


def get_player_match_stats_from_matches(discord_id: int) -> dict:
    """Calcula estatísticas de partidas de um jogador usando matches + match_players."""
    query = """
        SELECT mp.kills, mp.deaths, mp.assists, m.winner_team, mp.team
        FROM match_players mp
        JOIN matches m ON m.league_match_id = mp.league_match_id
        WHERE mp.discord_id = ?
    """
    params = (discord_id,)
    
    logger.info(f"Executing get_player_match_stats_from_matches query: {query.strip()} with params: {params}")
    
    with get_connection() as conn:
        # Buscar todas as partidas do jogador
        rows = conn.execute(query, params).fetchall()

    total_matches = len(rows)
    wins = 0
    losses = 0
    total_kills = 0
    total_deaths = 0
    total_assists = 0
    kda_rows = 0

    for row in rows:
        winner_team = row["winner_team"]
        player_team = row["team"]

        # Contar vitórias/derrotas
        if winner_team and player_team:
            if player_team == winner_team:
                wins += 1
            else:
                losses += 1

        # Somar KDA se disponível — cast defensivo pois SQLite pode ter string "?" salvo
        try:
            k = int(row["kills"]) if row["kills"] is not None else None
            d = int(row["deaths"]) if row["deaths"] is not None else None
            a = int(row["assists"]) if row["assists"] is not None else None
        except (ValueError, TypeError):
            k = d = a = None

        if k is not None and d is not None and a is not None:
            total_kills += k
            total_deaths += d
            total_assists += a
            kda_rows += 1

    winrate = (wins / total_matches * 100) if total_matches else 0
    return {
        "matches": total_matches,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "total_kills": total_kills,
        "total_deaths": total_deaths,
        "total_assists": total_assists,
        "kda_rows": kda_rows,
    }


def find_unregistered_match_players() -> list[dict]:
    """Retorna discord_ids presentes em match_players mas ausentes na tabela players."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT mp.discord_id,
                   mp.player_name,
                   COUNT(*) AS partidas
            FROM match_players mp
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.discord_id IS NOT NULL
              AND p.discord_id IS NULL
            GROUP BY mp.discord_id
            ORDER BY partidas DESC
        """).fetchall()
    return [dict(r) for r in rows]


def diagnose_and_fix_kda_data(fix: bool = False) -> dict:
    """Encontra linhas em match_players com KDA não-inteiro e opcionalmente corrige para 0."""
    with get_connection() as conn:
        bad_rows = conn.execute("""
            SELECT id, league_match_id, player_name, slot,
                   kills, deaths, assists,
                   typeof(kills)   AS kills_type,
                   typeof(deaths)  AS deaths_type,
                   typeof(assists) AS assists_type
            FROM match_players
            WHERE (kills   IS NOT NULL AND typeof(kills)   != 'integer')
               OR (deaths  IS NOT NULL AND typeof(deaths)  != 'integer')
               OR (assists IS NOT NULL AND typeof(assists) != 'integer')
        """).fetchall()

        results = [dict(r) for r in bad_rows]

        if fix and results:
            conn.execute("""
                UPDATE match_players SET kills = 0
                WHERE kills IS NOT NULL AND typeof(kills) != 'integer'
            """)
            conn.execute("""
                UPDATE match_players SET deaths = 0
                WHERE deaths IS NOT NULL AND typeof(deaths) != 'integer'
            """)
            conn.execute("""
                UPDATE match_players SET assists = 0
                WHERE assists IS NOT NULL AND typeof(assists) != 'integer'
            """)
            conn.commit()

    return {"bad_rows": results, "fixed": fix and len(results) > 0}


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


def _build_player_membership_clause(discord_id: int) -> tuple[str, tuple]:
    aliases = _get_player_alias_names(discord_id)
    clauses = ["mp.discord_id = ?"]
    params: list[object] = [discord_id]

    for alias in aliases:
        alias = alias.strip()
        if not alias:
            continue
        clauses.append("LOWER(mp.player_name) LIKE LOWER(?)")
        params.append(f"%{alias}%")

    membership_clause = f"({' OR '.join(clauses)})"
    logger.info(f"Built membership clause for discord_id {discord_id}: {membership_clause} with params: {params}")
    return membership_clause, tuple(params)


def get_player_top_heroes_from_matches(discord_id: int, limit: int = 5) -> list[dict]:
    """Retorna os heróis mais jogados por um jogador usando matches + match_players."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT mp.hero_name AS hero, COUNT(*) AS plays
        FROM match_players mp
        WHERE {membership_clause}
          AND mp.hero_name IS NOT NULL
          AND mp.hero_name != ''
        GROUP BY mp.hero_name
        ORDER BY plays DESC, mp.hero_name ASC
        LIMIT ?
    """
    params = params + (limit,)

    logger.info(f"Executing get_player_top_heroes_from_matches query: {query.strip()} with params: {params}")

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "hero": row["hero"],
            "plays": row["plays"]
        }
        for row in rows
    ]


def get_player_top_teammates_from_matches(discord_id: int, limit: int = 3) -> list[dict]:
    """Retorna os jogadores com quem mais jogou junto usando matches + match_players."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT
            mp2.discord_id,
            COALESCE(p.display_name, mp2.discord_id) AS display_name,
            COUNT(DISTINCT mp2.league_match_id) AS matches
        FROM match_players mp2
        JOIN players p ON p.discord_id = mp2.discord_id
        WHERE mp2.discord_id IS NOT NULL
          AND mp2.league_match_id IN (
              SELECT league_match_id FROM match_players mp WHERE {membership_clause}
          )
          AND mp2.discord_id != ?
        GROUP BY mp2.discord_id
        ORDER BY matches DESC, display_name ASC
        LIMIT ?
    """
    params = params + (discord_id, limit)

    logger.info(f"Executing get_player_top_teammates_from_matches query: {query.strip()} with params: {params}")

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


def get_player_top_opponents_from_matches(discord_id: int, result: str, limit: int = 3) -> list[dict]:
    """Retorna os jogadores contra quem mais jogou com determinado resultado usando matches + match_players."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT
            mp2.discord_id,
            COALESCE(p.display_name, mp2.discord_id) AS display_name,
            COUNT(DISTINCT mp2.league_match_id) AS matches
        FROM match_players mp2
        JOIN matches m ON m.league_match_id = mp2.league_match_id
        LEFT JOIN players p ON p.discord_id = mp2.discord_id
        WHERE mp2.discord_id IS NOT NULL
          AND mp2.league_match_id IN (
              SELECT league_match_id FROM match_players mp WHERE {membership_clause}
          )
          AND mp2.discord_id != ?
          AND (
              (? = 'win' AND mp2.team != m.winner_team)
              OR (? = 'loss' AND mp2.team = m.winner_team)
          )
        GROUP BY mp2.discord_id
        ORDER BY matches DESC, display_name ASC
        LIMIT ?
    """
    params = params + (discord_id, result, result, limit)

    logger.info(f"Executing get_player_top_opponents_from_matches query: {query.strip()} with params: {params}")

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


def get_player_top_heroes_with_winrate_from_matches(discord_id: int, limit: int = 5) -> list[dict]:
    """Retorna os heróis mais jogados com winrate calculada."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT
            mp.hero_name AS hero,
            COUNT(*) AS plays,
            SUM(CASE WHEN m.winner_team = mp.team THEN 1 ELSE 0 END) AS wins,
            CAST(SUM(CASE WHEN m.winner_team = mp.team THEN 1 ELSE 0 END) AS REAL)
                / COUNT(*) * 100.0 AS winrate
        FROM match_players mp
        JOIN matches m ON m.league_match_id = mp.league_match_id
        WHERE {membership_clause}
          AND mp.hero_name IS NOT NULL
          AND mp.hero_name != ''
        GROUP BY mp.hero_name
        ORDER BY plays DESC, winrate DESC
        LIMIT ?
    """
    params = params + (limit,)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "hero": row["hero"],
            "plays": row["plays"],
            "wins": row["wins"],
            "winrate": row["winrate"] or 0.0,
        }
        for row in rows
    ]


def get_player_head_to_head_from_matches(discord_id: int) -> list[dict]:
    """Retorna saldo de duelos diretos contra cada oponente identificado por discord_id."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        WITH player_matches AS (
            SELECT DISTINCT mp.league_match_id, mp.team AS player_team
            FROM match_players mp
            WHERE {membership_clause}
        )
        SELECT
            opp.discord_id,
            COALESCE(p.display_name, CAST(opp.discord_id AS TEXT)) AS display_name,
            COUNT(*) AS total,
            SUM(CASE WHEN m.winner_team = pm.player_team THEN 1 ELSE 0 END) AS player_wins,
            SUM(CASE WHEN m.winner_team = opp.team  THEN 1 ELSE 0 END) AS opponent_wins
        FROM player_matches pm
        JOIN match_players opp
            ON opp.league_match_id = pm.league_match_id
           AND opp.team != pm.player_team
           AND opp.discord_id IS NOT NULL
        JOIN matches m ON m.league_match_id = pm.league_match_id
        LEFT JOIN players p ON p.discord_id = opp.discord_id
        WHERE opp.discord_id != ?
        GROUP BY opp.discord_id
    """
    params = params + (discord_id,)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "discord_id": row["discord_id"],
            "display_name": str(row["display_name"]),
            "total": row["total"],
            "player_wins": row["player_wins"],
            "opponent_wins": row["opponent_wins"],
        }
        for row in rows
    ]


def get_player_top_win_teammates_from_matches(discord_id: int, limit: int = 3) -> list[dict]:
    """Retorna os companheiros com quem o jogador mais venceu partidas."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        WITH player_wins AS (
            SELECT DISTINCT mp.league_match_id, mp.team AS player_team
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            WHERE {membership_clause}
              AND mp.team = m.winner_team
        )
        SELECT
            mp2.discord_id,
            COALESCE(p.display_name, CAST(mp2.discord_id AS TEXT)) AS display_name,
            COUNT(*) AS wins_together
        FROM player_wins pw
        JOIN match_players mp2 ON mp2.league_match_id = pw.league_match_id
            AND mp2.team = pw.player_team
            AND mp2.discord_id IS NOT NULL
        LEFT JOIN players p ON p.discord_id = mp2.discord_id
        WHERE mp2.discord_id != ?
        GROUP BY mp2.discord_id
        ORDER BY wins_together DESC
        LIMIT ?
    """
    params = params + (discord_id, limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "discord_id": row["discord_id"],
            "display_name": str(row["display_name"]),
            "count": row["wins_together"],
        }
        for row in rows
    ]


def get_player_top_loss_teammates_from_matches(discord_id: int, limit: int = 3) -> list[dict]:
    """Retorna os companheiros com quem o jogador mais perdeu partidas."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        WITH player_losses AS (
            SELECT DISTINCT mp.league_match_id, mp.team AS player_team
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            WHERE {membership_clause}
              AND mp.team != m.winner_team
        )
        SELECT
            mp2.discord_id,
            COALESCE(p.display_name, CAST(mp2.discord_id AS TEXT)) AS display_name,
            COUNT(*) AS losses_together
        FROM player_losses pl
        JOIN match_players mp2 ON mp2.league_match_id = pl.league_match_id
            AND mp2.team = pl.player_team
            AND mp2.discord_id IS NOT NULL
        LEFT JOIN players p ON p.discord_id = mp2.discord_id
        WHERE mp2.discord_id != ?
        GROUP BY mp2.discord_id
        ORDER BY losses_together DESC
        LIMIT ?
    """
    params = params + (discord_id, limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "discord_id": row["discord_id"],
            "display_name": str(row["display_name"]),
            "count": row["losses_together"],
        }
        for row in rows
    ]


def get_player_match_history_from_matches(discord_id: int, limit: int = 20) -> list[dict]:
    """Retorna o histórico de partidas de um jogador usando matches + match_players."""
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT m.league_match_id, mp.hero_name, mp.kills, mp.deaths, mp.assists,
               m.winner_team, mp.team, m.created_at
        FROM match_players mp
        JOIN matches m ON m.league_match_id = mp.league_match_id
        WHERE {membership_clause}
        ORDER BY m.created_at DESC
        LIMIT ?
    """
    params = params + (limit,)

    logger.info(f"Executing get_player_match_history_from_matches query: {query.strip()} with params: {params}")

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "league_match_id": row["league_match_id"],
            "result": "win" if row["team"] == row["winner_team"] else "loss",
            "hero": row["hero_name"],
            "kills": row["kills"],
            "deaths": row["deaths"],
            "assists": row["assists"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_player_streak_from_matches(discord_id: int, max_events: int = 50) -> dict:
    """Calcula a sequência atual de vitórias ou derrotas usando matches + match_players."""
    history = get_player_match_history_from_matches(discord_id, max_events)
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


def get_streak_highlights_from_matches() -> dict:
    """
    Returns current and all-time win/loss streak leaders across all players.
    Each key maps to {"discord_id": int, "display_name": str, "count": int}.
    """
    with get_connection() as conn:
        players = conn.execute("""
            SELECT DISTINCT mp.discord_id,
                   COALESCE(p.display_name, CAST(mp.discord_id AS TEXT)) AS display_name
            FROM match_players mp
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.discord_id IS NOT NULL
        """).fetchall()

    current_win  = {"discord_id": None, "display_name": None, "count": 0}
    current_loss = {"discord_id": None, "display_name": None, "count": 0}
    record_win   = {"discord_id": None, "display_name": None, "count": 0}
    record_loss  = {"discord_id": None, "display_name": None, "count": 0}

    for player in players:
        discord_id   = player["discord_id"]
        display_name = str(player["display_name"])

        history = get_player_match_history_from_matches(discord_id, limit=500)
        if not history:
            continue

        # Current streak (history[0] is most recent)
        cur_type  = history[0]["result"]
        cur_count = 0
        for event in history:
            if event["result"] != cur_type:
                break
            cur_count += 1

        if cur_type == "win" and cur_count > current_win["count"]:
            current_win = {"discord_id": discord_id, "display_name": display_name, "count": cur_count}
        elif cur_type == "loss" and cur_count > current_loss["count"]:
            current_loss = {"discord_id": discord_id, "display_name": display_name, "count": cur_count}

        # All-time record: scan full history in chronological order
        max_win = max_loss = cur_w = cur_l = 0
        for event in reversed(history):
            if event["result"] == "win":
                cur_w += 1
                cur_l = 0
            else:
                cur_l += 1
                cur_w = 0
            if cur_w > max_win:
                max_win = cur_w
            if cur_l > max_loss:
                max_loss = cur_l

        if max_win > record_win["count"]:
            record_win = {"discord_id": discord_id, "display_name": display_name, "count": max_win}
        if max_loss > record_loss["count"]:
            record_loss = {"discord_id": discord_id, "display_name": display_name, "count": max_loss}

    return {
        "current_win":  current_win,
        "current_loss": current_loss,
        "record_win":   record_win,
        "record_loss":  record_loss,
    }


def get_player_match_history(discord_id: int, limit: int = 20) -> list[dict]:
    """Retorna os últimos eventos de partida de um jogador, do mais recente para o mais antigo."""
    query = """
        SELECT match_id, result, details, hero, kills, deaths, assists, created_at
        FROM match_history
        WHERE discord_id = ?
        ORDER BY match_id DESC
        LIMIT ?
    """
    params = (discord_id, limit)
    
    logger.info(f"Executing get_player_match_history query: {query.strip()} with params: {params}")
    
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "match_id": row["match_id"],
            "result": row["result"],
            "details": row["details"],
            "hero": row["hero"],
            "kills": row["kills"],
            "deaths": row["deaths"],
            "assists": row["assists"],
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
