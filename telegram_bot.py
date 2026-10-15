"""Telegram-бот «Адал Кеңесші» (aiogram 3).

Запуск:  python telegram_bot.py
"""
import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from app import analytics, memory, quiz
from app.assistant import get_assistant
from app.config import settings
from app.i18n import t
from app.llm import SpeechToText

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tg")

dp = Dispatcher()
stt = SpeechToText()
user_lang: dict[int, str] = {}          # выбранный язык интерфейса
quiz_score: dict[int, list[int]] = {}   # [правильных, всего]


def lang_of(user_id: int) -> str:
    return user_lang.get(user_id, "ru")


def rating_kb(query_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍", callback_data=f"rate:{query_id}:1"),
        InlineKeyboardButton(text="👎", callback_data=f"rate:{query_id}:-1"),
    ]])


async def answer_question(message: Message, text: str, user_id: int | None = None):
    user_id = user_id or message.from_user.id
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    ans = await asyncio.to_thread(get_assistant().ask, text, user_id, "telegram")
    user_lang.setdefault(user_id, ans.lang)
    markup = rating_kb(ans.query_id) if ans.status == "ok" else None
    await message.answer(ans.html, reply_markup=markup, disable_web_page_preview=True)


# ---------------- команды ----------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    lang = lang_of(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="lang:kk"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
    ]])
    text = t("start", lang)
    last = memory.recent_questions(message.from_user.id, 1)
    if last:  # пользователь уже общался с ботом — напоминаем, на чём остановились
        q = html.escape(last[0][0][:100])
        text = (f"👋 С возвращением! В прошлый раз вы спрашивали: «{q}». Можно продолжить — "
                f"я помню нашу беседу.\n\n" if lang == "ru" else
                f"👋 Қайта қош келдіңіз! Өткен жолы сіз сұрадыңыз: «{q}». Әңгімені жалғастыруға "
                f"болады — мен оны есте сақтадым.\n\n") + text
    await message.answer(text, reply_markup=kb)


@dp.message(Command("history"))
async def cmd_history(message: Message):
    lang = lang_of(message.from_user.id)
    items = memory.recent_questions(message.from_user.id, 5)
    if not items:
        await message.answer("История пока пуста." if lang == "ru" else "Тарих әзірге бос.")
        return
    head = "🕘 <b>Ваши последние вопросы:</b>" if lang == "ru" else "🕘 <b>Соңғы сұрақтарыңыз:</b>"
    lines = [head] + [f"{i}. {html.escape(q[:100])}" for i, (q, _) in enumerate(items, 1)]
    lines.append("\nЧтобы стереть историю — /forget" if lang == "ru" else "\nТарихты өшіру — /forget")
    await message.answer("\n".join(lines))


@dp.message(Command("forget"))
async def cmd_forget(message: Message):
    n = memory.forget(message.from_user.id)
    lang = lang_of(message.from_user.id)
    await message.answer(f"🧹 История удалена ({n} сообщений). Я больше не помню прошлые беседы."
                         if lang == "ru" else f"🧹 Тарих өшірілді ({n} хабар).")


@dp.message(Command("lang"))
async def cmd_lang(message: Message):
    await cmd_start(message)


@dp.callback_query(F.data.startswith("lang:"))
async def cb_lang(cb: CallbackQuery):
    lang = cb.data.split(":")[1]
    user_lang[cb.from_user.id] = lang
    await cb.message.answer(t("lang_set", lang) + "\n\n" + t("start", lang))
    await cb.answer()


@dp.message(Command("topics"))
async def cmd_topics(message: Message):
    lang = lang_of(message.from_user.id)
    rows = [[InlineKeyboardButton(text=title, callback_data=f"topic:{i}")]
            for i, (title, _, _) in enumerate(quiz.TOPICS)]
    await message.answer(t("topics", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("topic:"))
async def cb_topic(cb: CallbackQuery):
    _, ru_q, kk_q = quiz.TOPICS[int(cb.data.split(":")[1])]
    question = kk_q if lang_of(cb.from_user.id) == "kk" else ru_q
    await cb.answer()
    await cb.message.answer(f"❓ {question}")
    await answer_question(cb.message, question, user_id=cb.from_user.id)


@dp.message(Command("quiz"))
async def cmd_quiz(message: Message):
    await send_quiz(message, message.from_user.id)


async def send_quiz(message: Message, user_id: int):
    lang = lang_of(user_id)
    q = quiz.random_question()
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"quiz:{q['id']}:{i}")]
            for i, opt in enumerate(q["options"][lang])]
    await message.answer(f"🧠 <b>Викторина</b>\n\n{q['question'][lang]}",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("quiz:"))
async def cb_quiz(cb: CallbackQuery):
    _, qid, idx = cb.data.split(":")
    q = quiz.get(qid)
    lang = lang_of(cb.from_user.id)
    score = quiz_score.setdefault(cb.from_user.id, [0, 0])
    ok = int(idx) == q["correct"]
    score[0] += ok
    score[1] += 1
    verdict = ("✅ Верно!" if ok else "❌ Неверно.") if lang == "ru" else ("✅ Дұрыс!" if ok else "❌ Қате.")
    right = q["options"][lang][q["correct"]]
    src = f'\n📎 <a href="{q["source_url"]}">{q["source"]}</a>' if q.get("source_url") else ""
    score_txt = (f"Ваш счёт: {score[0]}/{score[1]}" if lang == "ru" else f"Сіздің ұпайыңыз: {score[0]}/{score[1]}")
    more = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="➡️ Следующий вопрос" if lang == "ru" else "➡️ Келесі сұрақ", callback_data="quiz_next")]])
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"{verdict} <b>{right}</b>\n\n{q['explain'][lang]}{src}\n\n🏆 {score_txt}",
                            reply_markup=more, disable_web_page_preview=True)
    await cb.answer()


@dp.callback_query(F.data == "quiz_next")
async def cb_quiz_next(cb: CallbackQuery):
    await cb.answer()
    await send_quiz(cb.message, cb.from_user.id)


@dp.callback_query(F.data.startswith("rate:"))
async def cb_rate(cb: CallbackQuery):
    _, qid, val = cb.data.split(":")
    analytics.rate(int(qid), int(val))
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(t("thanks", lang_of(cb.from_user.id)))


STATUS_LABELS = [("ok", "✅ Ответ найден"), ("no_answer", "🔍 Нет данных в базе"),
                 ("off_topic", "🚫 Не по теме"), ("injection", "🛡 Попытки взлома"),
                 ("greeting", "👋 Приветствия"), ("error", "⚠️ Ошибки")]


def _pct(part: int, total: int) -> str:
    return f"{round(part * 100 / total)}%" if total else "0%"


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in settings.admin_ids:
        return
    s = analytics.stats()
    total = s["total"]
    if not total:
        await message.answer("📊 Пока нет ни одного запроса.")
        return
    by_status, by_lang, by_ch = s["by_status"], s["by_lang"], s["by_channel"]

    lines = ["📊 <b>Аналитика «Адал Кеңесші»</b>", "",
             f"👥 Пользователей: <b>{s['users']}</b>",
             f"💬 Вопросов: <b>{total}</b> (за сутки: {s['today']})",
             f"⚡️ Среднее время ответа: <b>{s['avg_latency_ms'] / 1000:.1f} с</b>", "",
             "<b>Результаты</b>"]
    for key, label in STATUS_LABELS:
        if by_status.get(key):
            lines.append(f"{label} — {by_status[key]} ({_pct(by_status[key], total)})")

    lines += ["", "<b>Аудитория</b>",
              f"🇷🇺 Русский — {by_lang.get('ru', 0)} · 🇰🇿 Қазақша — {by_lang.get('kk', 0)}",
              f"📱 Telegram — {by_ch.get('telegram', 0)} · 🌐 Веб — {by_ch.get('web', 0)}"]
    rated = s["likes"] + s["dislikes"]
    lines.append(f"⭐️ Оценки: 👍 {s['likes']} · 👎 {s['dislikes']}"
                 + (f" ({_pct(s['likes'], rated)} положительных)" if rated else ""))

    if s["top_docs"]:
        lines += ["", "<b>📚 Популярные темы</b>"]
        lines += [f"{i}. {html.escape(t[:60])} — {c}" for i, (t, c) in enumerate(s["top_docs"][:5], 1)]
    if s["unanswered"]:
        lines += ["", "<b>❓ Не нашлось в базе</b> (что добавить)"]
        lines += [f"• {html.escape(q[:70])}" + (f" ×{c}" if c > 1 else "") for q, c in s["unanswered"]]
    await message.answer("\n".join(lines))


@dp.message(Command("stats_reset"))
async def cmd_stats_reset(message: Message):
    if message.from_user.id not in settings.admin_ids:
        return
    analytics.reset()
    await message.answer("🧹 Статистика очищена.")


# ---------------- голос и текст ----------------
@dp.message(F.voice | F.audio | F.video_note | F.video)
async def on_voice(message: Message):
    lang = lang_of(message.from_user.id)
    if not stt.enabled:
        await message.answer(t("voice_off", lang))
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    media = message.voice or message.audio or message.video_note or message.video
    fname = "voice.ogg" if (message.voice or message.audio) else "video.mp4"
    if getattr(media, "file_size", 0) and media.file_size > 20 * 1024 * 1024:
        await message.answer("Файл слишком большой (макс. 20 МБ)." if lang == "ru" else "Файл тым үлкен (ең көбі 20 МБ).")
        return
    if media.duration and media.duration > 120:
        await message.answer("Голосовое сообщение длиннее 2 минут." if lang == "ru" else "Дауыстық хабар 2 минуттан ұзын.")
        return
    try:
        file = await message.bot.get_file(media.file_id)
        data = await message.bot.download_file(file.file_path)
        text = await asyncio.to_thread(stt.transcribe, fname, data.read())
    except Exception as e:
        log.exception("STT error: %s", e)
        await message.answer(t("error", lang))
        return
    if not text:
        await message.answer(t("empty", lang))
        return
    await message.answer(t("voice_heard", lang, text=text))
    await answer_question(message, text)


@dp.message(F.text)
async def on_text(message: Message):
    await answer_question(message, message.text)


@dp.message()
async def on_other(message: Message):
    lang = lang_of(message.from_user.id)
    await message.answer("Пока я понимаю текст и голосовые сообщения." if lang == "ru"
                         else "Әзірге мен мәтін мен дауыстық хабарларды түсінемін.")


@dp.errors()
async def on_error(event):
    log.exception("Unhandled: %s", event.exception)
    return True


async def main():
    if not settings.telegram_token:
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN в .env")
    get_assistant()  # загружаем базу знаний заранее
    bot = Bot(settings.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать / Бастау"),
        BotCommand(command="topics", description="Темы / Тақырыптар"),
        BotCommand(command="quiz", description="Викторина"),
        BotCommand(command="history", description="История / Тарих"),
        BotCommand(command="forget", description="Стереть историю / Тарихты өшіру"),
        BotCommand(command="lang", description="Язык / Тіл"),
    ])
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
