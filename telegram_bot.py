"""Telegram-бот «Адал Кеңесші» (aiogram 3).

Запуск:  python telegram_bot.py
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from app import analytics, quiz
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
    await message.answer(t("start", lang), reply_markup=kb)


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


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in settings.admin_ids:
        return
    s = analytics.stats()
    unanswered = "\n".join(f"• {q[:80]}" for q in s["unanswered"]) or "—"
    await message.answer(
        f"📊 <b>Аналитика</b>\nЗапросов: {s['total']} | пользователей: {s['users']}\n"
        f"Статусы: {s['by_status']}\nЯзыки: {s['by_lang']}\n"
        f"Ср. время ответа: {s['avg_latency_ms']} мс\n👍 {s['likes']}  👎 {s['dislikes']}\n\n"
        f"<b>Без ответа (пополнить базу):</b>\n{unanswered}")


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
        BotCommand(command="lang", description="Язык / Тіл"),
    ])
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
