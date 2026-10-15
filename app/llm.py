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
        self.backup = OpenAI(base_url=settings.backup_base_url, api_key=settings.backup_api_key,
                             timeout=30, max_retries=1) \
            if self.enabled and settings.backup_api_key and settings.backup_model else None

    def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 2500) -> str:
        if not self.enabled:
            raise RuntimeError("LLM_API_KEY не задан")
        kwargs = dict(model=settings.llm_model, messages=messages,
                      temperature=temperature, max_tokens=max_tokens)
        if "anthropic.com" in settings.llm_base_url:  # Claude 4.7+ не принимает temperature < 1
            kwargs.pop("temperature")
        # «Думающие» модели (Gemini 2.5) тратят лимит токенов на скрытые рассуждения и могут
        # вернуть пустой ответ. Для справочного бота рассуждения не нужны — отключаем их.
        if settings.llm_reasoning_effort:
            kwargs["reasoning_effort"] = settings.llm_reasoning_effort
        try:
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if "reasoning" not in str(e).lower() or "reasoning_effort" not in kwargs:
                    raise
                kwargs.pop("reasoning_effort")  # провайдер не поддерживает параметр — без него
                resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            if not self.backup:
                raise
            # Основная модель недоступна (лимит запросов, сбой сети) — спрашиваем резервную
            log.warning("Основная LLM недоступна (%s), используем резервную %s", str(e)[:120],
                        settings.backup_model)
            kwargs.pop("reasoning_effort", None)
            kwargs["model"] = settings.backup_model
            kwargs["max_tokens"] = min(max_tokens, 2000)
            resp = self.backup.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        if choice.finish_reason == "length":  # ответ упёрся в лимит — не показываем оборванную фразу
            text = trim_to_sentence(text)
        return text

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=settings.emb_model, input=texts)
        return [d.embedding for d in resp.data]


def trim_to_sentence(text: str) -> str:
    """Убирает оборванный хвост: последнюю недописанную строку списка или предложение."""
    text = text.rstrip()
    lines = text.split("\n")
    if len(lines) > 1:                      # оборван последний пункт — убираем его целиком
        return "\n".join(lines[:-1]).rstrip()
    cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
    return text[: cut + 1] if cut > 0 else text + "…"


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
