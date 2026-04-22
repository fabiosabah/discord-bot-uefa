# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime
from itertools import groupby
from typing import Any

from core.db.audit_repo import log_action
from core.db.connection import get_connection, _sanitize_hero_name
from core.db.ocr_repo import get_match_screenshot, set_match_screenshot_status
from core.db.player_repo import resolve_player_names_exact, _get_player_alias_names

logger = logging.getLogger("Database")


# ─────────────────────────────────────────────
# Hero stats
# ─────────────────────────────────────────────

def get_all_hero_stats_from_matches() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                h.name                                                                  AS hero,
                COUNT(mp.id)                                                            AS picks,
                COALESCE(SUM(CASE WHEN mp.team = m.winner_team THEN 1 ELSE 0 END), 0)  AS wins
            FROM heroes h
            LEFT JOIN match_players mp ON mp.hero_name = h.name
            LEFT JOIN matches m        ON m.league_match_id = mp.league_match_id
            GROUP BY h.name
            ORDER BY picks DESC, wins * 1.0 / NULLIF(COUNT(mp.id), 0) DESC
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


# ─────────────────────────────────────────────
# Match insert / import
# ─────────────────────────────────────────────

def _resolve_ocr_player_discord_ids(
    players: list[dict[str, Any]],
    player_mapping: dict[str, dict[str, object]],
) -> None:
    if not players:
        return

    lookup_names = []
    for player in players:
        if not isinstance(player, dict):
            continue
        player_name = (
            player.get("player_name") or player.get("name") or player.get("player") or ""
        ).strip()
        if not player_name or player.get("discord_id") is not None:
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
            player.get("player_name") or player.get("name") or player.get("player") or ""
        ).strip()
        if not player_name or player.get("discord_id") is not None:
            continue
        discord_id = resolved.get(player_name)
        if discord_id is not None:
            player["discord_id"] = discord_id


def insert_ocr_match(
    job_id: int,
    player_mapping: dict[str, dict[str, object]],
    admin_id: int,
    admin_name: str,
) -> int:
    from core.ocr import _normalize_team, generate_match_hash

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
        created_at=created_at,
    )

    insert_match_history_from_ocr_import(audit_id, match_id, league_match_id, details, created_at)

    parsed["imported_by"] = admin_id
    parsed["match_id"] = match_id
    parsed["league_match_id"] = league_match_id
    parsed["match_hash"] = match_hash
    parsed["mapping"] = player_mapping
    set_match_screenshot_status(job_id, "imported", metadata=json.dumps(parsed, ensure_ascii=False))
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
    created_at: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO match_imports "
            "(match_id, steam_match_id, dota_match_id, match_date, mode, winner, duration, radiant_score, dire_score, raw_metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (match_id, steam_match_id, dota_match_id, match_date, mode, winner, duration, radiant_score, dire_score, raw_metadata, created_at),
        )
        conn.commit()


def insert_match_history_from_ocr_import(
    audit_id: int, match_id: int, league_match_id: int, details: str, created_at: str
) -> int:
    with get_connection() as conn:
        match_row = conn.execute(
            "SELECT winner_team FROM matches WHERE league_match_id = ?", (league_match_id,)
        ).fetchone()
        if not match_row:
            return 0

        winner_team = match_row["winner_team"]
        player_rows = conn.execute(
            "SELECT discord_id, hero_name, kills, deaths, assists, team FROM match_players WHERE league_match_id = ?",
            (league_match_id,),
        ).fetchall()

        inserted = 0
        for player in player_rows:
            discord_id = player["discord_id"]
            team = player["team"]
            if discord_id is None or team is None or winner_team is None:
                continue
            result = "win" if team == winner_team else "loss"
            conn.execute(
                "INSERT OR IGNORE INTO match_history "
                "(audit_id, match_id, discord_id, result, details, hero, kills, deaths, assists, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (audit_id, match_id, discord_id, result, details, player["hero_name"],
                 player["kills"], player["deaths"], player["assists"], created_at),
            )
            inserted += 1
        conn.commit()
    logger.info(f"[DB] Gravado match_history OCR para league_match_id {league_match_id} com {inserted} registros.")
    return inserted


def insert_league_match(
    parsed: dict[str, Any],
    match_hash: str,
    external_match_id: str | None = None,
) -> int:
    from core.ocr import _normalize_team

    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    winner_team = _normalize_team((match_info.get("winner_team") or match_info.get("winner") or "").strip())
    duration = match_info.get("duration")
    match_datetime = match_info.get("datetime")
    score = match_info.get("score") or {}
    radiant_score = score.get("radiant")
    dire_score = score.get("dire")
    created_at = datetime.now().isoformat()

    def _parse_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    players_data = parsed.get("players_data") or parsed.get("players") or []
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO matches "
            "(match_hash, external_match_id, winner_team, duration, match_datetime, score_radiant, score_dire, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (match_hash, external_match_id, winner_team, duration, match_datetime, radiant_score, dire_score, created_at),
        )
        if cursor.rowcount == 0:
            row = conn.execute(
                "SELECT league_match_id FROM matches WHERE match_hash = ?", (match_hash,)
            ).fetchone()
            if row:
                raise ValueError("Partida Duplicada")
            raise RuntimeError("Falha ao recuperar league_match_id para partida duplicada.")

        league_match_id = cursor.lastrowid

        for index, player in enumerate(players_data, start=1):
            if not isinstance(player, dict):
                continue
            player_name = (player.get("player_name") or player.get("name") or player.get("player") or "").strip()
            hero_name = _sanitize_hero_name(
                (player.get("hero_name") or player.get("hero") or "") or None
            )

            kills = deaths = assists = None
            if any(player.get(k) is not None for k in ("kills", "deaths", "assists")):
                kills  = _parse_int(player.get("kills"))
                deaths = _parse_int(player.get("deaths"))
                assists = _parse_int(player.get("assists"))
            else:
                raw_kda = player.get("kda") or player.get("score")
                if isinstance(raw_kda, dict):
                    kills   = _parse_int(raw_kda.get("kills") or raw_kda.get("kill"))
                    deaths  = _parse_int(raw_kda.get("deaths") or raw_kda.get("death"))
                    assists = _parse_int(raw_kda.get("assists") or raw_kda.get("assist"))
                else:
                    raw_text = str(raw_kda).strip() if raw_kda is not None else ""
                    if "/" in raw_text:
                        parts   = raw_text.split("/")
                        kills   = _parse_int(parts[0] if len(parts) > 0 else None)
                        deaths  = _parse_int(parts[1] if len(parts) > 1 else None)
                        assists = _parse_int(parts[2] if len(parts) > 2 else None)

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
                "INSERT INTO match_players "
                "(league_match_id, slot, player_name, discord_id, hero_name, kills, deaths, assists, networth, team) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (league_match_id, index, player_name, discord_id, hero_name, kills, deaths, assists, networth, team),
            )
        conn.commit()

    return league_match_id


# ─────────────────────────────────────────────
# Match reads
# ─────────────────────────────────────────────

def get_match_by_league_id(league_match_id: int) -> dict | None:
    with get_connection() as conn:
        match_row = conn.execute(
            "SELECT league_match_id, match_hash, external_match_id, winner_team, duration, match_datetime, score_radiant, score_dire, created_at "
            "FROM matches WHERE league_match_id = ?",
            (league_match_id,),
        ).fetchone()
        if not match_row:
            return None
        player_rows = conn.execute(
            "SELECT slot, player_name, discord_id, hero_name, kills, deaths, assists, networth, team "
            "FROM match_players WHERE league_match_id = ? ORDER BY slot",
            (league_match_id,),
        ).fetchall()
    return {
        "league_match_id":  match_row["league_match_id"],
        "match_hash":        match_row["match_hash"],
        "external_match_id": match_row["external_match_id"],
        "match_info": {
            "winner_team": match_row["winner_team"],
            "duration":    match_row["duration"],
            "datetime":    match_row["match_datetime"],
            "match_id":    match_row["external_match_id"],
            "score":       {"radiant": match_row["score_radiant"], "dire": match_row["score_dire"]},
        },
        "players_data": [
            {
                "slot":        row["slot"],
                "player_name": row["player_name"],
                "discord_id":  row["discord_id"],
                "hero_name":   row["hero_name"],
                "kills":       row["kills"],
                "deaths":      row["deaths"],
                "assists":     row["assists"],
                "networth":    row["networth"],
                "team":        row["team"],
            }
            for row in player_rows
        ],
        "created_at": match_row["created_at"],
    }


def get_ranking_from_matches() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                mp.discord_id,
                COALESCE(p.display_name, CAST(mp.discord_id AS TEXT)) AS display_name,
                COUNT(*)                                               AS games,
                SUM(CASE WHEN mp.team = m.winner_team THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN mp.team != m.winner_team THEN 1 ELSE 0 END) AS losses
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.discord_id IS NOT NULL
            GROUP BY mp.discord_id
            ORDER BY
                (SUM(CASE WHEN mp.team = m.winner_team THEN 1 ELSE 0 END) * 3
                 - SUM(CASE WHEN mp.team != m.winner_team THEN 1 ELSE 0 END)) DESC,
                wins DESC
        """).fetchall()
    return [
        {
            "discord_id":   row["discord_id"],
            "display_name": str(row["display_name"]),
            "wins":         row["wins"],
            "losses":       row["losses"],
            "points":       row["wins"] * 3 - row["losses"],
            "games":        row["games"],
        }
        for row in rows
    ]


def get_last_ocr_match_info() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT league_match_id, created_at FROM matches ORDER BY league_match_id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_match_created_at(league_match_id: int) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT created_at FROM matches WHERE league_match_id = ? LIMIT 1", (league_match_id,)
        ).fetchone()
    return row["created_at"] if row else None


# ─────────────────────────────────────────────
# Match mutations
# ─────────────────────────────────────────────

def get_next_match_id() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(match_id) AS max_match_id FROM ("
            "SELECT match_id FROM match_history UNION ALL SELECT match_id FROM match_imports)"
        ).fetchone()
    return (row["max_match_id"] or 0) + 1


def update_league_match_heroes(league_match_id: int, hero_names: list[str]) -> int:
    with get_connection() as conn:
        updated = 0
        for slot, hero_name in enumerate(hero_names, start=1):
            sanitized = _sanitize_hero_name(hero_name) if hero_name is not None else None
            cursor = conn.execute(
                "UPDATE match_players SET hero_name = ? WHERE league_match_id = ? AND slot = ?",
                (sanitized, league_match_id, slot),
            )
            updated += cursor.rowcount if hasattr(cursor, "rowcount") else 0
        conn.commit()
    if updated:
        logger.info(f"[DB] Atualizados {updated} heróis para league_match_id {league_match_id}.")
    return updated


def update_league_match_hero_by_slot(league_match_id: int, slot: int, hero_name: str) -> bool:
    sanitized = _sanitize_hero_name(hero_name)
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE match_players SET hero_name = ? WHERE league_match_id = ? AND slot = ?",
            (sanitized, league_match_id, slot),
        )
        conn.commit()
    updated = cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if updated:
        logger.info(f"[DB] Hero do slot {slot} atualizado para league_match_id {league_match_id}: {sanitized}")
    return updated > 0


def update_league_match_player_names(league_match_id: int, player_names: list[str]) -> int:
    with get_connection() as conn:
        updated = 0
        for slot, player_name in enumerate(player_names, start=1):
            cursor = conn.execute(
                "UPDATE match_players SET player_name = ? WHERE league_match_id = ? AND slot = ?",
                (player_name.strip(), league_match_id, slot),
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
            (player_name.strip(), league_match_id, slot),
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
            (duration.strip(), league_match_id),
        )
        conn.commit()
    updated = cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if updated:
        logger.info(f"[DB] Duração da partida {league_match_id} atualizada para: {duration}")
    return updated > 0


def delete_league_match(league_match_id: int) -> bool:
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM matches WHERE league_match_id = ? LIMIT 1", (league_match_id,)
        ).fetchone()
        if not exists:
            return False
        conn.execute("DELETE FROM match_players WHERE league_match_id = ?", (league_match_id,))
        conn.execute("DELETE FROM matches WHERE league_match_id = ?", (league_match_id,))
        conn.commit()
    logger.info(f"[DB] Partida importada league_match_id {league_match_id} removida.")
    return True


# ─────────────────────────────────────────────
# Player stats from OCR matches
# ─────────────────────────────────────────────

def _build_player_membership_clause(discord_id: int) -> tuple[str, tuple]:
    aliases = _get_player_alias_names(discord_id)
    clauses = ["mp.discord_id = ?"]
    params: list[object] = [discord_id]
    for alias in aliases:
        alias = alias.strip()
        if alias:
            clauses.append("LOWER(mp.player_name) LIKE LOWER(?)")
            params.append(f"%{alias}%")
    membership_clause = f"({' OR '.join(clauses)})"
    logger.debug(f"Built membership clause for discord_id {discord_id}: {membership_clause}")
    return membership_clause, tuple(params)


def get_player_match_stats_from_matches(discord_id: int) -> dict:
    query = """
        SELECT mp.kills, mp.deaths, mp.assists, m.winner_team, mp.team
        FROM match_players mp
        JOIN matches m ON m.league_match_id = mp.league_match_id
        WHERE mp.discord_id = ?
    """
    with get_connection() as conn:
        rows = conn.execute(query, (discord_id,)).fetchall()

    wins = losses = total_kills = total_deaths = total_assists = kda_rows = 0
    for row in rows:
        if row["winner_team"] and row["team"]:
            if row["team"] == row["winner_team"]:
                wins += 1
            else:
                losses += 1
        try:
            k = int(row["kills"])  if row["kills"]   is not None else None
            d = int(row["deaths"]) if row["deaths"]  is not None else None
            a = int(row["assists"])if row["assists"]  is not None else None
        except (ValueError, TypeError):
            k = d = a = None
        if k is not None and d is not None and a is not None:
            total_kills += k; total_deaths += d; total_assists += a; kda_rows += 1

    total_matches = len(rows)
    return {
        "matches": total_matches, "wins": wins, "losses": losses,
        "winrate": wins / total_matches * 100 if total_matches else 0,
        "total_kills": total_kills, "total_deaths": total_deaths,
        "total_assists": total_assists, "kda_rows": kda_rows,
    }


def get_player_top_heroes_from_matches(discord_id: int, limit: int = 5) -> list[dict]:
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT mp.hero_name AS hero, COUNT(*) AS plays
        FROM match_players mp
        WHERE {membership_clause} AND mp.hero_name IS NOT NULL AND mp.hero_name != ''
        GROUP BY mp.hero_name
        ORDER BY plays DESC, mp.hero_name ASC
        LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(query, params + (limit,)).fetchall()
    return [{"hero": r["hero"], "plays": r["plays"]} for r in rows]


def get_player_top_teammates_from_matches(discord_id: int, limit: int = 3) -> list[dict]:
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT
            mp2.discord_id,
            COALESCE(p.display_name, mp2.discord_id) AS display_name,
            COUNT(DISTINCT mp2.league_match_id) AS matches
        FROM match_players mp2
        JOIN players p ON p.discord_id = mp2.discord_id
        WHERE mp2.discord_id IS NOT NULL
          AND mp2.league_match_id IN (SELECT league_match_id FROM match_players mp WHERE {membership_clause})
          AND mp2.discord_id != ?
        GROUP BY mp2.discord_id
        ORDER BY matches DESC, display_name ASC
        LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(query, params + (discord_id, limit)).fetchall()
    return [{"discord_id": r["discord_id"], "display_name": r["display_name"], "count": r["matches"]} for r in rows]


def get_player_top_opponents_from_matches(discord_id: int, result: str, limit: int = 3) -> list[dict]:
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
          AND mp2.league_match_id IN (SELECT league_match_id FROM match_players mp WHERE {membership_clause})
          AND mp2.discord_id != ?
          AND ((? = 'win' AND mp2.team != m.winner_team) OR (? = 'loss' AND mp2.team = m.winner_team))
        GROUP BY mp2.discord_id
        ORDER BY matches DESC, display_name ASC
        LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(query, params + (discord_id, result, result, limit)).fetchall()
    return [{"discord_id": r["discord_id"], "display_name": r["display_name"], "count": r["matches"]} for r in rows]


def get_player_top_heroes_with_winrate_from_matches(discord_id: int, limit: int = 5) -> list[dict]:
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        SELECT
            mp.hero_name AS hero,
            COUNT(*) AS plays,
            SUM(CASE WHEN m.winner_team = mp.team THEN 1 ELSE 0 END) AS wins,
            CAST(SUM(CASE WHEN m.winner_team = mp.team THEN 1 ELSE 0 END) AS REAL) / COUNT(*) * 100.0 AS winrate
        FROM match_players mp
        JOIN matches m ON m.league_match_id = mp.league_match_id
        WHERE {membership_clause} AND mp.hero_name IS NOT NULL AND mp.hero_name != ''
        GROUP BY mp.hero_name
        ORDER BY plays DESC, winrate DESC
        LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(query, params + (limit,)).fetchall()
    return [{"hero": r["hero"], "plays": r["plays"], "wins": r["wins"], "winrate": r["winrate"] or 0.0} for r in rows]


def get_player_head_to_head_from_matches(discord_id: int) -> list[dict]:
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
            SUM(CASE WHEN m.winner_team = opp.team       THEN 1 ELSE 0 END) AS opponent_wins
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
    with get_connection() as conn:
        rows = conn.execute(query, params + (discord_id,)).fetchall()
    return [
        {
            "discord_id":    row["discord_id"],
            "display_name":  str(row["display_name"]),
            "total":         row["total"],
            "player_wins":   row["player_wins"],
            "opponent_wins": row["opponent_wins"],
        }
        for row in rows
    ]


def get_player_teammate_balance_from_matches(
    discord_id: int, min_games: int = 3, limit: int = 3
) -> list[dict]:
    membership_clause, params = _build_player_membership_clause(discord_id)
    query = f"""
        WITH player_games AS (
            SELECT DISTINCT mp.league_match_id, mp.team AS player_team
            FROM match_players mp
            WHERE {membership_clause}
        )
        SELECT
            mp2.discord_id,
            COALESCE(p.display_name, CAST(mp2.discord_id AS TEXT)) AS display_name,
            COUNT(*) AS games_together,
            SUM(CASE WHEN mp2.team = m.winner_team THEN 1 ELSE 0 END) AS wins_together,
            SUM(CASE WHEN mp2.team != m.winner_team THEN 1 ELSE 0 END) AS losses_together,
            SUM(CASE WHEN mp2.team = m.winner_team THEN 1 ELSE -1 END) AS balance
        FROM player_games pg
        JOIN match_players mp2 ON mp2.league_match_id = pg.league_match_id
            AND mp2.team = pg.player_team
            AND mp2.discord_id IS NOT NULL
        JOIN matches m ON m.league_match_id = pg.league_match_id
        LEFT JOIN players p ON p.discord_id = mp2.discord_id
        WHERE mp2.discord_id != ?
        GROUP BY mp2.discord_id
        HAVING games_together >= ?
        ORDER BY balance DESC
    """
    with get_connection() as conn:
        rows = conn.execute(query, params + (discord_id, min_games)).fetchall()
    return [
        {
            "discord_id":  row["discord_id"],
            "display_name": str(row["display_name"]),
            "games":   row["games_together"],
            "wins":    row["wins_together"],
            "losses":  row["losses_together"],
            "balance": row["balance"],
        }
        for row in rows
    ]


def get_player_duo_stats(discord_id_a: int, discord_id_b: int) -> list[dict]:
    query = """
        WITH a_games AS (
            SELECT mp.league_match_id, mp.team AS team, mp.hero_name AS hero
            FROM match_players mp
            WHERE mp.discord_id = ?
        ),
        b_games AS (
            SELECT mp.league_match_id, mp.team AS team, mp.hero_name AS hero
            FROM match_players mp
            WHERE mp.discord_id = ?
        )
        SELECT
            m.league_match_id,
            a.team      AS team_a,
            b.team      AS team_b,
            a.hero      AS hero_a,
            b.hero      AS hero_b,
            m.winner_team,
            m.created_at,
            m.duration
        FROM a_games a
        JOIN b_games b ON b.league_match_id = a.league_match_id
        JOIN matches m ON m.league_match_id = a.league_match_id
        ORDER BY m.league_match_id DESC
    """
    with get_connection() as conn:
        rows = conn.execute(query, (discord_id_a, discord_id_b)).fetchall()
    return [dict(r) for r in rows]


def _duration_to_seconds(d: str) -> int | None:
    parts = d.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return None


def _format_duration(d: str) -> str:
    secs = _duration_to_seconds(d)
    if secs is None:
        return d
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_match_duration_extremes(min_seconds: int = 60) -> dict:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT league_match_id, duration, winner_team,
                   score_radiant, score_dire, created_at
            FROM matches
            WHERE duration IS NOT NULL AND duration != ''
        """).fetchall()

    parsed = []
    for row in rows:
        secs = _duration_to_seconds(row["duration"])
        if secs is None or secs < min_seconds:
            continue
        parsed.append({**dict(row), "seconds": secs, "display_duration": _format_duration(row["duration"])})

    parsed.sort(key=lambda x: x["seconds"])
    return {
        "fastest": parsed[:5],
        "longest": list(reversed(parsed[-5:])),
    }


def fix_malformed_durations() -> int:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT league_match_id, duration FROM matches WHERE duration IS NOT NULL AND duration != ''"
        ).fetchall()
        fixed = 0
        for row in rows:
            corrected = _format_duration(row["duration"])
            if corrected != row["duration"]:
                conn.execute(
                    "UPDATE matches SET duration = ? WHERE league_match_id = ?",
                    (corrected, row["league_match_id"])
                )
                fixed += 1
        if fixed:
            conn.commit()
    return fixed


def get_match_players_bulk(league_match_ids: list[int]) -> dict[int, list[dict]]:
    if not league_match_ids:
        return {}
    placeholders = ",".join("?" * len(league_match_ids))
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT mp.league_match_id, mp.team, mp.hero_name,
                   COALESCE(p.display_name, mp.player_name) AS display_name
            FROM match_players mp
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.league_match_id IN ({placeholders})
            ORDER BY mp.league_match_id, mp.slot
        """, league_match_ids).fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(row["league_match_id"], []).append(dict(row))
    return result


def get_player_match_history_from_matches(discord_id: int, limit: int = 20) -> list[dict]:
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
    with get_connection() as conn:
        rows = conn.execute(query, params + (limit,)).fetchall()
    return [
        {
            "league_match_id": row["league_match_id"],
            "result":  "win" if row["team"] == row["winner_team"] else "loss",
            "hero":    row["hero_name"],
            "kills":   row["kills"],
            "deaths":  row["deaths"],
            "assists": row["assists"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_player_streak_from_matches(discord_id: int, max_events: int = 50) -> dict:
    history = get_player_match_history_from_matches(discord_id, max_events)
    if not history:
        return {"streak_type": None, "streak_count": 0, "recent": []}
    streak_type = history[0]["result"]
    streak_count = 0
    for event in history:
        if event["result"] != streak_type:
            break
        streak_count += 1
    return {"streak_type": streak_type, "streak_count": streak_count, "recent": history[:5]}


def get_streak_highlights_from_matches() -> dict:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                mp.discord_id,
                COALESCE(p.display_name, CAST(mp.discord_id AS TEXT)) AS display_name,
                CASE WHEN mp.team = m.winner_team THEN 'win' ELSE 'loss' END AS result,
                m.created_at
            FROM match_players mp
            JOIN matches m ON m.league_match_id = mp.league_match_id
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.discord_id IS NOT NULL
            ORDER BY mp.discord_id, m.created_at DESC
        """).fetchall()

    players_history: dict[int, tuple[str, list[str]]] = {}
    for discord_id, group in groupby(rows, key=lambda r: r["discord_id"]):
        events = list(group)
        display_name = str(events[0]["display_name"])
        players_history[discord_id] = (display_name, [e["result"] for e in events])

    current_win  = {"discord_id": None, "display_name": None, "count": 0}
    current_loss = {"discord_id": None, "display_name": None, "count": 0}
    record_win   = {"discord_id": None, "display_name": None, "count": 0}
    record_loss  = {"discord_id": None, "display_name": None, "count": 0}

    for discord_id, (display_name, results) in players_history.items():
        if not results:
            continue

        cur_type = results[0]
        cur_count = 0
        for r in results:
            if r != cur_type:
                break
            cur_count += 1

        if cur_type == "win" and cur_count > current_win["count"]:
            current_win = {"discord_id": discord_id, "display_name": display_name, "count": cur_count}
        elif cur_type == "loss" and cur_count > current_loss["count"]:
            current_loss = {"discord_id": discord_id, "display_name": display_name, "count": cur_count}

        max_win = max_loss = cur_w = cur_l = 0
        for r in reversed(results):
            if r == "win":
                cur_w += 1; cur_l = 0
            else:
                cur_l += 1; cur_w = 0
            if cur_w > max_win:   max_win = cur_w
            if cur_l > max_loss:  max_loss = cur_l

        if max_win  > record_win["count"]:
            record_win  = {"discord_id": discord_id, "display_name": display_name, "count": max_win}
        if max_loss > record_loss["count"]:
            record_loss = {"discord_id": discord_id, "display_name": display_name, "count": max_loss}

    return {
        "current_win":  current_win,
        "current_loss": current_loss,
        "record_win":   record_win,
        "record_loss":  record_loss,
    }


# ─────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────

def find_unregistered_match_players() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT mp.discord_id, mp.player_name, COUNT(*) AS partidas
            FROM match_players mp
            LEFT JOIN players p ON p.discord_id = mp.discord_id
            WHERE mp.discord_id IS NOT NULL AND p.discord_id IS NULL
            GROUP BY mp.discord_id
            ORDER BY partidas DESC
        """).fetchall()
    return [dict(r) for r in rows]


def diagnose_and_fix_kda_data(fix: bool = False) -> dict:
    with get_connection() as conn:
        bad_rows = conn.execute("""
            SELECT id, league_match_id, player_name, slot,
                   kills, deaths, assists,
                   typeof(kills) AS kills_type, typeof(deaths) AS deaths_type, typeof(assists) AS assists_type
            FROM match_players
            WHERE (kills   IS NOT NULL AND typeof(kills)   != 'integer')
               OR (deaths  IS NOT NULL AND typeof(deaths)  != 'integer')
               OR (assists IS NOT NULL AND typeof(assists) != 'integer')
        """).fetchall()
        results = [dict(r) for r in bad_rows]
        if fix and results:
            for col in ("kills", "deaths", "assists"):
                conn.execute(f"UPDATE match_players SET {col} = 0 WHERE {col} IS NOT NULL AND typeof({col}) != 'integer'")
            conn.commit()
    return {"bad_rows": results, "fixed": fix and len(results) > 0}
