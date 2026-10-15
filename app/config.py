"""Настройки приложения. Все секреты читаются из переменных окружения / файла .env,
в репозиторий они не попадают (п. 6.4 и 13.2 Положения)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # LLM — любой OpenAI-совместимый API (Gemini, OpenAI, Groq, локальная Llama через Ollama)
    llm_base_url: str = _env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    llm_api_key: str = _env("LLM_API_KEY")
    llm_model: str = _env("LLM_MODEL", "gemini-2.5-flash")
    # Для Gemini отключаем «размышления» (иначе возможны пустые ответы); пусто = не передавать
    llm_reasoning_effort: str = _env(
        "LLM_REASONING_EFFORT", "none" if "googleapis" in _env("LLM_BASE_URL", "googleapis") else "")

    # Эмбеддинги (необязательно). Пусто -> работает только BM25.
    emb_model: str = _env("EMBEDDINGS_MODEL", "")

    # Распознавание голоса (Whisper через OpenAI или Groq)
    stt_base_url: str = _env("STT_BASE_URL", "https://api.groq.com/openai/v1")
    stt_api_key: str = _env("STT_API_KEY")
    stt_model: str = _env("STT_MODEL", "whisper-large-v3")

    telegram_token: str = _env("TELEGRAM_BOT_TOKEN")
    admin_ids: tuple = field(default_factory=lambda: tuple(
        int(x) for x in _env("ADMIN_IDS").replace(" ", "").split(",") if x.isdigit()))
    admin_token: str = _env("ADMIN_TOKEN", "")  # пароль к /stats в веб-версии

    kb_dir: Path = ROOT / "kb"
    data_dir: Path = ROOT / "data"

    top_k: int = int(_env("TOP_K", "8"))
    # Ниже этого порога считаем, что в базе знаний ответа нет (п. 4.9)
    min_score: float = float(_env("MIN_SCORE", "0.12"))


settings = Settings()
settings.data_dir.mkdir(exist_ok=True)
