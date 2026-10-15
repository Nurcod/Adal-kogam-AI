"""Клиенты к моделям. Используется OpenAI-совместимый протокол, поэтому одна и та же
кодовая база работает с Gemini, OpenAI, Groq (Llama) или локальной моделью через Ollama —
провайдер меняется только переменными в .env."""
import logging

from openai import OpenAI

from app.config import settings

log = logging.getLogger(__name__)


class LLM:
    def __init__(self):
        self.enabled = bool(settings.llm_api_key)
        self.client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "none",
                             timeout=40, max_retries=2) if self.enabled else None

    def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 1200) -> str:
        if not self.enabled:
            raise RuntimeError("LLM_API_KEY не задан")
        kwargs = dict(model=settings.llm_model, messages=messages,
                      temperature=temperature, max_tokens=max_tokens)
        # «Думающие» модели (Gemini 2.5) тратят лимит токенов на скрытые рассуждения и могут
        # вернуть пустой ответ. Для справочного бота рассуждения не нужны — отключаем их.
        if settings.llm_reasoning_effort:
            kwargs["reasoning_effort"] = settings.llm_reasoning_effort
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            if "reasoning" not in str(e).lower() or "reasoning_effort" not in kwargs:
                raise
            kwargs.pop("reasoning_effort")  # провайдер не поддерживает параметр — без него
            resp = self.client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=settings.emb_model, input=texts)
        return [d.embedding for d in resp.data]


class SpeechToText:
    """Голосовые сообщения -> текст (Whisper; понимает казахский и русский)."""

    def __init__(self):
        self.enabled = bool(settings.stt_api_key)
        self.client = OpenAI(base_url=settings.stt_base_url, api_key=settings.stt_api_key,
                             timeout=60) if self.enabled else None

    def transcribe(self, filename: str, data: bytes) -> str:
        if not self.enabled:
            raise RuntimeError("STT_API_KEY не задан")
        resp = self.client.audio.transcriptions.create(model=settings.stt_model, file=(filename, data))
        return (resp.text or "").strip()
