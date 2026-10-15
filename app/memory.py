"""Долговременная память диалогов (персонализация).

Бот помнит, что пользователь обсуждал раньше — даже после перезапуска сервера:
последние реплики подставляются в запрос к модели, поэтому можно продолжить
разговор («а что там было про президента?») на следующий день.

Храним только хэш ID пользователя и текст реплик, срок хранения — 30 дней.
Пользователь может стереть свою историю командой /forget.
"""
import sqlite3
import threading
import time

from app.analytics import user_hash
from app.config import settings

KEEP_DAYS = 30
CONTEXT_MESSAGES = 6      # сколько последних реплик передаём модели
CONTEXT_MAX_AGE_H = 72    # старше — не подставляем в контекст (тема могла смениться)

_lock = threading.Lock()
_c = sqlite3.connect(settings.data_dir / "analytics.db", check_same_thread=False)
_c.execute("""CREATE TABLE IF NOT EXISTS dialog(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_hash TEXT, role TEXT, content TEXT, ts REAL)""")
_c.execute("CREATE INDEX IF NOT EXISTS dialog_user ON dialog(user_hash, ts)")
_c.execute("DELETE FROM dialog WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
_c.commit()


def add(user_id, role: str, content: str):
    with _lock:
        _c.execute("INSERT INTO dialog(user_hash, role, content, ts) VALUES(?,?,?,?)",
                   (user_hash(user_id), role, content[:1500], time.time()))
        _c.commit()


def context(user_id) -> list[tuple[str, str]]:
    """Последние реплики для передачи модели (в хронологическом порядке)."""
    with _lock:
        rows = _c.execute(
            "SELECT role, content FROM dialog WHERE user_hash=? AND ts>=? ORDER BY ts DESC LIMIT ?",
            (user_hash(user_id), time.time() - CONTEXT_MAX_AGE_H * 3600, CONTEXT_MESSAGES)).fetchall()
    return list(reversed(rows))


def recent_questions(user_id, n: int = 5) -> list[tuple[str, float]]:
    with _lock:
        return _c.execute(
            "SELECT content, ts FROM dialog WHERE user_hash=? AND role='user' ORDER BY ts DESC LIMIT ?",
            (user_hash(user_id), n)).fetchall()


def forget(user_id) -> int:
    with _lock:
        n = _c.execute("DELETE FROM dialog WHERE user_hash=?", (user_hash(user_id),)).rowcount
        _c.commit()
    return n
