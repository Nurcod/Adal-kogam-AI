"""Аналитика (SQLite): какие вопросы задают, на что бот не нашёл ответ, оценки 👍/👎.
Храним только технический ID пользователя (без имён и телефонов) — минимизация персональных данных."""
import hashlib
import sqlite3
import threading
import time

from app.config import settings

_DB = settings.data_dir / "analytics.db"
_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(_DB, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS queries(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, channel TEXT, user_hash TEXT, lang TEXT,
        question TEXT, status TEXT, latency_ms INTEGER, top_doc TEXT, rating INTEGER DEFAULT 0)""")
    return conn


_c = _conn()


def user_hash(user_id) -> str:
    return hashlib.sha256(f"adal:{user_id}".encode()).hexdigest()[:16]


def log_query(channel: str, user_id, lang: str, question: str, status: str, latency_ms: int, top_doc: str) -> int:
    with _lock:
        cur = _c.execute(
            "INSERT INTO queries(ts,channel,user_hash,lang,question,status,latency_ms,top_doc) VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), channel, user_hash(user_id), lang, question[:500], status, latency_ms, top_doc))
        _c.commit()
        return cur.lastrowid


def rate(query_id: int, value: int):
    with _lock:
        _c.execute("UPDATE queries SET rating=? WHERE id=?", (value, query_id))
        _c.commit()


def stats() -> dict:
    with _lock:
        q = lambda sql: _c.execute(sql).fetchall()
        total = q("SELECT COUNT(*) FROM queries")[0][0]
        return {
            "total": total,
            "users": q("SELECT COUNT(DISTINCT user_hash) FROM queries")[0][0],
            "by_status": dict(q("SELECT status, COUNT(*) FROM queries GROUP BY status")),
            "by_lang": dict(q("SELECT lang, COUNT(*) FROM queries GROUP BY lang")),
            "by_channel": dict(q("SELECT channel, COUNT(*) FROM queries GROUP BY channel")),
            "avg_latency_ms": round(q("SELECT AVG(latency_ms) FROM queries WHERE status='ok'")[0][0] or 0),
            "likes": q("SELECT COUNT(*) FROM queries WHERE rating=1")[0][0],
            "dislikes": q("SELECT COUNT(*) FROM queries WHERE rating=-1")[0][0],
            "top_docs": q("""SELECT top_doc, COUNT(*) c FROM queries WHERE status='ok' AND top_doc!=''
                             GROUP BY top_doc ORDER BY c DESC LIMIT 7"""),
            # Вопросы без ответа — подсказка, какие документы добавить в базу знаний
            "unanswered": [r[0] for r in q("""SELECT question FROM queries WHERE status='no_answer'
                                              ORDER BY ts DESC LIMIT 10""")],
        }
