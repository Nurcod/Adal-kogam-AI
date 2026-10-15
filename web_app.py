"""Веб-интерфейс «Адал Кеңесші» (FastAPI) + REST API.

Запуск:  uvicorn web_app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import logging
import time
from collections import defaultdict, deque

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import analytics, quiz
from app.assistant import get_assistant
from app.config import ROOT, settings
from app.i18n import t
from app.llm import SpeechToText

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Adal Kenesshi API", version="1.0")
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
stt = SpeechToText()

# --- простое ограничение частоты запросов (защита от спама и перерасхода API) ---
_RATE: dict[str, deque] = defaultdict(deque)
RATE_LIMIT, RATE_WINDOW = 20, 60


def check_rate(ip: str):
    now, q = time.time(), _RATE[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "Слишком много запросов. Подождите минуту.")
    q.append(now)


class ChatIn(BaseModel):
    question: str = Field(..., max_length=2000)
    session_id: str = Field("web", max_length=64)


class RateIn(BaseModel):
    query_id: int
    value: int = Field(..., ge=-1, le=1)


@app.on_event("startup")
def _warmup():
    get_assistant()


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/health")
def health():
    a = get_assistant()
    return {"status": "ok", "chunks": len(a.retriever.chunks), "llm": a.llm.enabled, "voice": stt.enabled}


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    check_rate(request.client.host if request.client else "?")
    ans = await asyncio.to_thread(get_assistant().ask, body.question, f"web:{body.session_id}", "web")
    return {"status": ans.status, "lang": ans.lang, "html": ans.html, "sources": ans.sources,
            "constitution": ans.constitution, "query_id": ans.query_id, "latency_ms": ans.latency_ms,
            "fallback": ans.fallback}


@app.post("/api/voice")
async def voice(request: Request, file: UploadFile = File(...)):
    check_rate(request.client.host if request.client else "?")
    if not stt.enabled:
        raise HTTPException(503, t("voice_off", "ru"))
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Файл слишком большой")
    try:
        text = await asyncio.to_thread(stt.transcribe, file.filename or "voice.webm", data)
    except Exception:
        raise HTTPException(502, t("error", "ru"))
    return {"text": text}


@app.post("/api/rate")
def rate(body: RateIn):
    analytics.rate(body.query_id, body.value)
    return {"ok": True}


@app.get("/api/topics")
def topics():
    return [{"title": a, "ru": b, "kk": c} for a, b, c in quiz.TOPICS]


@app.get("/api/quiz")
def quiz_question():
    q = quiz.random_question()
    return {k: q[k] for k in ("id", "question", "options")}


@app.get("/api/quiz/{qid}/{answer}")
def quiz_check(qid: str, answer: int):
    q = quiz.get(qid)
    if not q:
        raise HTTPException(404)
    return {"correct": q["correct"], "ok": answer == q["correct"], "explain": q["explain"],
            "source": q.get("source"), "source_url": q.get("source_url")}


@app.get("/api/stats")
def stats(token: str = ""):
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(403, "Нужен ADMIN_TOKEN")
    return analytics.stats()
