# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime

from core.db.connection import get_connection

logger = logging.getLogger("Database")


def log_action(
    admin_id: int,
    admin_name: str,
    command: str,
    details: str,
    affected_ids: list[int] | None = None,
    created_at: str | None = None,
) -> int:
    affected_json = json.dumps(affected_ids) if affected_ids else None
    created_at = created_at or datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO audit_log (admin_id, admin_name, command, details, affected_ids, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (admin_id, admin_name, command, details, affected_json, created_at),
        )
        conn.commit()
        audit_id = cursor.lastrowid
    logger.info(f"[AUDIT] {admin_name} usou '{command}': {details}")
    return audit_id


def count_match_deletions_today(admin_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM audit_log WHERE admin_id = ? AND command = '!apagarmatch' AND created_at >= ?",
            (admin_id, today),
        ).fetchone()
    return row["cnt"] if row else 0


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
            "audit_id":     row["audit_id"],
            "command":      row["command"],
            "details":      row["details"],
            "affected_ids": json.loads(row["affected_ids"]) if row["affected_ids"] else [],
            "created_at":   row["created_at"],
            "players":      row["players"].split(',') if row["players"] else [],
            "match_id":     row["match_id"],
            "result":       row["result"],
        }
        for row in rows
    ]


def get_last_update() -> str | None:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT created_at FROM audit_log
            WHERE command IN ('!venceu', '!perdeu')
            ORDER BY id DESC LIMIT 1
        """).fetchone()
    return row["created_at"] if row else None
