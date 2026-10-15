"""Главный конвейер ответа (общий для Telegram и веб-версии):

  вопрос -> предпроверка (guard) -> язык -> переформулировка запроса (LLM)
        -> поиск по базе знаний (RAG) -> проверка порога релевантности
        -> генерация ответа строго по найденным фрагментам (LLM)
        -> пометка «закреплено в Конституции» + список источников
"""
import html
import logging
import re
import time
from dataclasses import dataclass, field

from app import analytics, memory
from app.config import settings
from app.guard import precheck
from app.i18n import detect_lang, t
from app.llm import LLM
from app.prompts import CHAT_PROMPT, REWRITE_PROMPT, SYSTEM_PROMPT, build_context
from app.rag.loader import load_kb
from app.rag.retriever import Retriever

log = logging.getLogger(__name__)


@dataclass
class Answer:
    status: str                 # ok | no_answer | off_topic | injection | empty | too_long | greeting | error
    lang: str
    html: str                   # готовый к показу текст (HTML-разметка Telegram)
    sources: list = field(default_factory=list)       # [(name, url)]
    constitution: list = field(default_factory=list)  # где закреплено в Конституции
    query_id: int = 0
    latency_ms: int = 0
    fallback: bool = False      # ответ выдан без LLM (резервный режим)


# Статусы, на которые бот отвечает живой репликой модели, а не шаблоном
CHAT_STATUSES = {"greeting", "smalltalk", "thanks_reply"}


def md_to_html(text: str) -> str:
    """Безопасное преобразование ответа модели в HTML для Telegram/веба."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^\s*[\-\*]\s+", "• ", text)
    text = re.sub(r"(?m)^#+\s*", "", text)
    return text.strip()


class Assistant:
    def __init__(self):
        self.llm = LLM()
        chunks = load_kb(settings.kb_dir)
        embedder = self.llm.embed if (self.llm.enabled and settings.emb_model) else None
        try:
            self.retriever = Retriever(chunks, embedder=embedder)
        except Exception as e:  # нет доступа к эмбеддингам — работаем на BM25
            log.warning("Эмбеддинги недоступны (%s), используется только BM25", e)
            self.retriever = Retriever(chunks)
        log.info("База знаний: %d фрагментов из %d документов", len(chunks),
                 len({c.doc_id for c in chunks}))

    # ------------------------------------------------------------------
    def _rewrite(self, question: str, hist: list) -> str:
        if not self.llm.enabled:
            return ""
        msgs = [{"role": "system", "content": REWRITE_PROMPT}]
        for role, content in list(hist)[-4:]:
            msgs.append({"role": role, "content": content[:400]})
        msgs.append({"role": "user", "content": question})
        try:
            return self.llm.chat(msgs, temperature=0, max_tokens=60).split("\n")[0][:200]
        except Exception as e:
            log.warning("rewrite failed: %s", e)
            return ""

    def _finish(self, ans: Answer, channel, user_id, question, t0, top_doc="") -> Answer:
        ans.latency_ms = int((time.time() - t0) * 1000)
        ans.query_id = analytics.log_query(channel, user_id, ans.lang, question, ans.status,
                                           ans.latency_ms, top_doc)
        return ans

    def ask(self, question: str, user_id="anon", channel="web", lang: str | None = None) -> Answer:
        t0 = time.time()
        question = (question or "").strip()
        lang = detect_lang(question) if question else (lang or "ru")
        ui_lang = lang if lang in ("ru", "kk") else "ru"

        hist = memory.context(user_id)  # прошлые реплики пользователя (персонализация)
        problem = precheck(question)
        if problem in CHAT_STATUSES:  # живое общение вместо шаблона
            return self._finish(self._chat_answer(problem, question, hist, ui_lang, user_id),
                                channel, user_id, question, t0)
        if problem:
            return self._finish(Answer(problem, ui_lang, t(problem, ui_lang)), channel, user_id, question, t0)
        rewritten = self._rewrite(question, hist)
        hits = self.retriever.search([question, rewritten], k=settings.top_k)

        best_cov = max((h.coverage for h in hits), default=0)
        best_cos = max((h.cosine for h in hits), default=0)
        if not hits or (best_cov < settings.min_score and best_cos < 0.55):
            if self._is_off_topic(question):
                return self._finish(self._chat_answer("off_topic", question, hist, ui_lang, user_id),
                                    channel, user_id, question, t0)
            return self._finish(Answer("no_answer", ui_lang, t("no_answer", ui_lang)),
                                channel, user_id, question, t0)

        top_doc = hits[0].chunk.title
        if not self.llm.enabled:
            return self._finish(self._extractive(hits, ui_lang), channel, user_id, question, t0, top_doc)
        try:
            raw = self._generate(question, hits, hist)
        except Exception as e:
            log.exception("LLM error: %s", e)
            ans = self._extractive(hits, ui_lang)
            return self._finish(ans, channel, user_id, question, t0, top_doc)

        if raw.strip().startswith("OFF_TOPIC"):
            return self._finish(self._chat_answer("off_topic", question, hist, ui_lang, user_id),
                                channel, user_id, question, t0)
        if raw.strip().startswith("NO_ANSWER") or not raw.strip():
            return self._finish(Answer("no_answer", ui_lang, t("no_answer", ui_lang)),
                                channel, user_id, question, t0)

        memory.add(user_id, "user", question)
        memory.add(user_id, "assistant", raw)
        ans = self._decorate(raw, hits, ui_lang)
        return self._finish(ans, channel, user_id, question, t0, top_doc)

    def _chat_answer(self, status: str, question: str, hist: list, lang: str, user_id) -> Answer:
        """Живой ответ на приветствие, благодарность, реплику или вопрос не по теме.
        Модель ведёт беседу, но фактов не сообщает; если она недоступна — заготовленный текст."""
        if self.llm.enabled:
            msgs = [{"role": "system", "content": CHAT_PROMPT}]
            for role, content in list(hist)[-4:]:
                msgs.append({"role": role, "content": content[:600]})
            msgs.append({"role": "user", "content": question})
            try:
                reply = self.llm.chat(msgs, temperature=0.6, max_tokens=300)
                if reply and not reply.startswith(("OFF_TOPIC", "NO_ANSWER", "NONE")):
                    memory.add(user_id, "user", question)
                    memory.add(user_id, "assistant", reply)
                    return Answer(status, lang, md_to_html(reply))
            except Exception as e:
                log.warning("chat reply failed: %s", e)
        key = "thanks_reply" if status == "thanks_reply" else status
        return Answer(status, lang, t(key, lang))

    def _is_off_topic(self, question: str) -> bool:
        """Если в базе ничего не нашлось — уточняем у модели, вопрос вообще по теме или нет,
        чтобы показать правильное сообщение («не по теме» vs «нет подтверждённых данных»)."""
        if not self.llm.enabled:
            return False
        try:
            verdict = self.llm.chat([
                {"role": "system", "content": "Относится ли вопрос к Конституции, законам, государственному "
                 "устройству, внутренней политике, общественным ценностям, правовой грамотности или "
                 "противодействию коррупции в Казахстане? Ответь одним словом: YES или NO."},
                {"role": "user", "content": question}], temperature=0, max_tokens=3)
            return verdict.strip().upper().startswith("NO")
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _generate(self, question, hits, hist) -> str:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for role, content in list(hist)[-4:]:
            msgs.append({"role": role, "content": content[:800]})
        msgs.append({"role": "user", "content": f"КОНТЕКСТ:\n{build_context(hits)}\n\nВОПРОС: {question}"})
        return self.llm.chat(msgs)

    def _decorate(self, raw: str, hits, lang: str, fallback=False) -> Answer:
        cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", raw) if 0 < int(n) <= len(hits)})
        used = [hits[n - 1] for n in cited] or hits[:1]

        # Пометка о Конституции ставится детерминированно по метаданным базы знаний,
        # а не «на усмотрение» модели — это гарантирует требование п. 4.1 Положения.
        constitution = []
        for h in used:
            if h.chunk.in_constitution and h.chunk.in_constitution not in constitution:
                constitution.append(h.chunk.in_constitution)

        sources, seen = [], set()
        for h in used:
            for s in h.chunk.sources[:2]:
                key = s.url or s.name
                if key not in seen:
                    seen.add(key)
                    sources.append((s.name, s.url))

        body = md_to_html(raw)
        parts = [body]
        if constitution:
            parts.append(t("constitution_badge", lang, where=html.escape("; ".join(constitution))))
        if sources:
            lines = [f"📎 <b>{t('sources', lang)}:</b>"]
            for name, url in sources[:4]:
                lines.append(f'• <a href="{html.escape(url)}">{html.escape(name)}</a>' if url
                             else f"• {html.escape(name)}")
            parts.append("\n".join(lines))
        return Answer("ok", lang, "\n\n".join(parts), sources, constitution, fallback=fallback)

    def _extractive(self, hits, lang) -> Answer:
        """Резервный режим, если модель недоступна: показываем дословную выдержку из
        самого релевантного официального фрагмента (без генерации — значит, без искажений)."""
        top = hits[0].chunk
        note = ("📄 Выдержка из официального документа по вашему вопросу:"
                if lang == "ru" else "📄 Сұрағыңыз бойынша ресми құжаттан үзінді:")
        # убираем служебные строки «Источник: URL» — источники показываются отдельным блоком
        clean = "\n".join(l for l in top.text.split("\n")
                          if not re.match(r"\s*(Источник|Источники|Дереккөз)\w*\s*:", l)
                          and not re.match(r"\s*https?://", l))
        clean = re.sub(r"\s*\(?https?://[^\s)]+\)?", "", clean).strip()
        excerpt = clean[:900] + ("…" if len(clean) > 900 else "")
        raw = f"{note}\n\n**{top.title}{' — ' + top.label if top.label else ''}**\n{excerpt} [1]"
        return self._decorate(raw, hits[:1], lang, fallback=True)


_assistant: Assistant | None = None


def get_assistant() -> Assistant:
    global _assistant
    if _assistant is None:
        _assistant = Assistant()
    return _assistant
