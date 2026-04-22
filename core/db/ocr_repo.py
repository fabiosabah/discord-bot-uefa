# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from core.db.connection import get_connection

logger = logging.getLogger("Database")


def enqueue_match_screenshot(
    message_id: int,
    guild_id: int,
    channel_id: int,
    author_id: int,
    image_url: str,
    created_at: str,
) -> int:
    import requests

    logger.info(f"[DB] Enqueuing screenshot: message_id={message_id}, url={image_url}")
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        image_data = response.content
        logger.info(f"[DB] Downloaded image: {len(image_data)} bytes from {image_url}")
    except Exception as e:
        logger.error(f"[DB] Failed to download image from {image_url}: {e}")
        raise

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO match_screenshots (message_id, guild_id, channel_id, author_id, image_url, image_data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, author_id, image_url, image_data, created_at),
        )
        conn.commit()
        job_id = cursor.lastrowid
    logger.info(f"[DB] Screenshot enqueued: job {job_id} ({image_url})")
    return job_id


def is_match_screenshot_enqueued(message_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM match_screenshots WHERE message_id = ? LIMIT 1", (message_id,)
        ).fetchone()
    return bool(row)


def get_pending_match_screenshots(limit: int = 20) -> list[dict]:
    logger.debug(f"[DB] Fetching up to {limit} pending screenshot jobs")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, message_id, guild_id, channel_id, author_id, image_url, image_data, status, metadata, created_at "
            "FROM match_screenshots WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    jobs = [dict(row) for row in rows]
    logger.debug(f"[DB] Found {len(jobs)} pending jobs")
    return jobs


def set_match_screenshot_status(job_id: int, status: str, metadata: str | None = None) -> None:
    logger.debug(f"[DB] Updating job {job_id} status to '{status}'")
    with get_connection() as conn:
        conn.execute(
            "UPDATE match_screenshots SET status = ?, metadata = ?, processed_at = ? WHERE id = ?",
            (status, metadata, datetime.now().isoformat(), job_id),
        )
        conn.commit()
    logger.info(f"[DB] Screenshot job {job_id} marked as {status}.")


def get_match_screenshot(job_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, message_id, guild_id, channel_id, author_id, image_url, image_data, status, metadata, created_at, processed_at "
            "FROM match_screenshots WHERE id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


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


def delete_match_screenshots() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM match_screenshots")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'match_screenshots'")
        conn.commit()
    logger.info("[DB] Histórico de screenshots apagado.")
