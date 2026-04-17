# -*- coding: utf-8 -*-
import sqlite3
import logging
import os
import json
import re
from datetime import datetime
from typing import Any
from core.config import DB_PATH
from core.dota_heroes import resolve_hero_name

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
                hero_name        TEXT,
                kda              TEXT,
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
                hero_name        TEXT,
                kda              TEXT,
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
        logger.info("[DB] Tabela 'match_history' criada ou já existente.")

        mh_columns = conn.execute("PRAGMA table_info(match_history)").fetchall()
        mh_column_names = [col["name"] for col in mh_columns]
        if "match_id" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN match_id INTEGER")
            logger.info("[DB] Coluna 'match_id' adicionada via migration ao match_history.")

        if "hero" not in mh_column_names:
            conn.execute("ALTER TABLE match_history ADD COLUMN hero TEXT")
            logger.info("[DB] Coluna 'hero' adicionada via migration ao match_history.")

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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_imports_match_date
            ON match_imports(match_date)
        """)
        logger.info("[DB] Índices de match_history criados ou já existentes.")

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

    from core.ocr import _normalize_team

    winner = parsed.get("winner")
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

        for index, player in enumerate(players_data, start=1):
            if not isinstance(player, dict):
                continue
            player_name = (player.get("player_name") or player.get("name") or player.get("player") or "").strip()
            hero_name = _sanitize_hero_name((player.get("hero_name") or player.get("hero") or "") if player.get("hero_name") or player.get("hero") else None)
            kda = player.get("kda") or player.get("score") or ""
            networth = player.get("networth") or player.get("net_worth")
            try:
                networth = int(networth) if networth is not None and str(networth).strip() != "" else None
            except (TypeError, ValueError):
                networth = None
            team = _normalize_team(player.get("team") or player.get("side"))
            conn.execute(
                "INSERT INTO match_players (league_match_id, slot, player_name, hero_name, kda, networth, team) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (league_match_id, index, player_name, hero_name, kda, networth, team)
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
            "SELECT slot, player_name, hero_name, kda, networth, team FROM match_players WHERE league_match_id = ? ORDER BY slot",
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
                "hero_name": row["hero_name"],
                "kda": row["kda"],
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
        created_at = datetime.now().isoformat()

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
        row = conn.execute("SELECT MAX(match_id) AS max_match_id FROM match_history").fetchone()
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


def record_match_history(audit_id: int, affected_ids: list[int], result: str, details: str, created_at: str, match_id: int, hero: str | None = None):
    if not affected_ids:
        logger.info(f"[DB] Sem IDs afetados para gravar match_history do audit {audit_id}.")
        return

    inserted = 0
    with get_connection() as conn:
        for discord_id in affected_ids:
            conn.execute("""
                INSERT OR IGNORE INTO match_history
                (audit_id, match_id, discord_id, result, details, hero, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (audit_id, match_id, discord_id, result, details, hero, created_at))
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


def log_match_action(admin_id: int, admin_name: str, command: str, details: str, affected_ids: list[int] = None) -> int:
    created_at = datetime.now().isoformat()
    audit_id = log_action(admin_id, admin_name, command, details, affected_ids, created_at=created_at)
    result = "win" if command == "!venceu" else "loss"
    pending_match_id = get_pending_match_id_for_opposite_result(result)
    if pending_match_id is None:
        pending_match_id = get_pending_match_id_for_same_side(result)
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
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'match_history'")
        conn.commit()
    logger.info("[DB] Histórico de partidas apagado.")


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
    logger.info(f"[DB] Screenshot job {job_id} removido.")


def enqueue_match_screenshot(message_id: int, guild_id: int, channel_id: int, author_id: int, image_url: str, created_at: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO match_screenshots (message_id, guild_id, channel_id, author_id, image_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, author_id, image_url, created_at)
        )
        conn.commit()
        job_id = cursor.lastrowid
    logger.info(f"[DB] Screenshot enfileirada: job {job_id} ({image_url})")
    return job_id


def is_match_screenshot_enqueued(message_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM match_screenshots WHERE message_id = ? LIMIT 1",
            (message_id,)
        ).fetchone()
    return bool(row)


def get_pending_match_screenshots(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, message_id, guild_id, channel_id, author_id, image_url, status, metadata, created_at FROM match_screenshots WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def set_match_screenshot_status(job_id: int, status: str, metadata: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE match_screenshots SET status = ?, metadata = ?, processed_at = ? WHERE id = ?",
            (status, metadata, datetime.now().isoformat(), job_id)
        )
        conn.commit()
    logger.info(f"[DB] Screenshot job {job_id} marcado como {status}.")


def get_match_screenshot(job_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, message_id, guild_id, channel_id, author_id, image_url, status, metadata, created_at, processed_at FROM match_screenshots WHERE id = ?",
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
