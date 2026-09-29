from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from ..sanitize import sanitize_text
from .paths import private_file, state_dir

_DB_LOCK = threading.RLock()
_KEY_LOCKS_LOCK = threading.Lock()
_KEY_LOCKS: dict[tuple[str, int], threading.Lock] = {}
_MEMORY: dict[tuple[str, int], dict[str, Any]] = {}
_MISSES: set[tuple[str, int]] = set()


def _db_path() -> Path:
    return state_dir() / "transcripts.sqlite3"


def _ensure_db() -> Path:
    path = _db_path()
    with _DB_LOCK:
        con = sqlite3.connect(path)
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    transcript TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                )
                """
            )
            con.commit()
        finally:
            con.close()
        private_file(path)
    return path


def get_cached_transcript(chat_id: str | int, message_id: int) -> Optional[dict[str, Any]]:
    key = (str(chat_id), int(message_id))
    with _DB_LOCK:
        cached = _MEMORY.get(key)
        if cached is not None:
            return dict(cached)
        if key in _MISSES:
            return None
        path = _ensure_db()
        con = sqlite3.connect(path)
        try:
            row = con.execute(
                "SELECT transcript, provider, model, created_at FROM transcripts "
                "WHERE chat_id = ? AND message_id = ?",
                key,
            ).fetchone()
        finally:
            con.close()
        if row is None:
            _MISSES.add(key)
            return None
        value = {
            "transcript": sanitize_text(row[0], limit=30000),
            "provider": row[1],
            "model": row[2],
            "created_at": row[3],
        }
        _MEMORY[key] = value
        return dict(value)


def save_transcript(
    chat_id: str | int,
    message_id: int,
    transcript: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    key = (str(chat_id), int(message_id))
    clean = sanitize_text(transcript, limit=30000).strip()
    if not clean:
        raise ValueError("transcript is empty")
    value = {
        "transcript": clean,
        "provider": sanitize_text(provider, limit=128) if provider else None,
        "model": sanitize_text(model, limit=128) if model else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _DB_LOCK:
        path = _ensure_db()
        con = sqlite3.connect(path)
        try:
            con.execute(
                """
                INSERT INTO transcripts(chat_id, message_id, transcript, provider, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    transcript = excluded.transcript,
                    provider = excluded.provider,
                    model = excluded.model,
                    created_at = excluded.created_at
                """,
                (key[0], key[1], value["transcript"], value["provider"], value["model"], value["created_at"]),
            )
            con.commit()
        finally:
            con.close()
        private_file(path)
        _MISSES.discard(key)
        _MEMORY[key] = dict(value)
    return dict(value)


def _key_lock(chat_id: str | int, message_id: int) -> threading.Lock:
    key = (str(chat_id), int(message_id))
    with _KEY_LOCKS_LOCK:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


@asynccontextmanager
async def transcript_lock(chat_id: str | int, message_id: int) -> AsyncIterator[None]:
    """Collapse concurrent transcriptions of the same Telegram message."""
    lock = _key_lock(chat_id, message_id)
    await asyncio.to_thread(lock.acquire)
    try:
        yield
    finally:
        lock.release()
