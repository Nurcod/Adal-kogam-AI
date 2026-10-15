# Адал Кеңесші — AI-навигатор по внутренней политике Казахстана

Telegram-бот и веб-приложение для конкурса «Адал қоғам». Отвечает на вопросы о Конституции РК 2026,
законах и Посланиях Главы государства на казахском и русском — только по официальным документам
и со ссылкой на источник.

- Telegram: [@Adal_kogam_ai_bot](https://t.me/Adal_kogam_ai_bot)
- Веб-версия: http://159.198.43.231:8095

---

## 1. Инструкция по запуску

**Требования:** Python 3.11+, git.

**Шаг 1. Скачать проект и установить зависимости**
```bash
git clone https://github.com/Nurcod/Adal-kogam-AI.git
cd Adal-kogam-AI
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Шаг 2. Указать ключи**
```bash
cp .env.example .env
```
Открыть `.env` и заполнить:

| Переменная | Где взять |
|---|---|
| `LLM_API_KEY` | ключ Claude API — console.anthropic.com → API keys |
| `STT_API_KEY` | ключ Groq (голос и резервная модель) — console.groq.com/keys |
| `TELEGRAM_BOT_TOKEN` | у @BotFather в Telegram |
| `ADMIN_IDS` | свой Telegram ID (узнать у @userinfobot) — для команды `/stats` |

Файл `.env` в репозиторий не попадает.

**Шаг 3. Загрузить полные тексты законов** (Конституция РК 2026, закон о противодействии коррупции)
```bash
python scripts/fetch_sources.py
```

**Шаг 4. Проверить и запустить**
```bash
python -m pytest -q                      # автотесты (работают без ключей)
python telegram_bot.py                   # Telegram-бот
uvicorn web_app:app --port 8095          # веб-версия → http://localhost:8095
```

**Шаг 5. Развёртывание на сервере (VPS Namecheap, работает 24/7)**

1. В панели Namecheap заказать VPS с ОС **Ubuntu**. IP-адрес и пароль root придут на почту.
2. Подключиться к серверу и установить системные пакеты:
```bash
ssh root@IP_СЕРВЕРА
apt update && apt install -y git python3-venv
```
3. Выполнить шаги 1–3 в папке `/opt`:
```bash
cd /opt && git clone https://github.com/Nurcod/Adal-kogam-AI.git && cd Adal-kogam-AI
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # вписать ключи
venv/bin/python scripts/fetch_sources.py
```
4. Создать два сервиса systemd — бот и сайт будут запускаться вместе с сервером и сами перезапускаться при сбое:
```bash
cat > /etc/systemd/system/adal-bot.service <<'EOF'
[Unit]
Description=Adal Kenesshi Telegram bot
After=network-online.target

[Service]
WorkingDirectory=/opt/Adal-kogam-AI
ExecStart=/opt/Adal-kogam-AI/venv/bin/python telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/adal-web.service <<'EOF'
[Unit]
Description=Adal Kenesshi web
After=network-online.target

[Service]
WorkingDirectory=/opt/Adal-kogam-AI
ExecStart=/opt/Adal-kogam-AI/venv/bin/uvicorn web_app:app --host 0.0.0.0 --port 8095
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now adal-bot adal-web
```
5. Проверить: `systemctl status adal-bot adal-web` — оба `active (running)`; сайт открывается по адресу `http://IP_СЕРВЕРА:8095`. Если не открывается — разрешить порт: `ufw allow 8095`.

**Обновление после изменений в коде:**
```bash
cd /opt/Adal-kogam-AI && git pull && systemctl restart adal-bot adal-web
```
Логи: `journalctl -u adal-bot -f`

**Альтернатива — Docker:** `docker compose up --build` (запускает бота и веб-версию).

---

## 2. Используемые технологии

| Назначение | Технология |
|---|---|
| Язык | Python 3.11+ |
| Telegram-бот | aiogram 3 |
| Веб-сервер и API | FastAPI + Uvicorn |
| Веб-интерфейс | HTML, CSS, JavaScript (без фреймворков) |
| Основная AI-модель | Claude Sonnet 5.5 (Anthropic) |
| Резервная AI-модель | Llama 3.3 70B (Groq) — если основная недоступна |
| Распознавание голоса | Whisper large-v3 (Groq) |
| Распознавание изображений | Claude Sonnet 5.5 (vision) |
| Поиск по документам (RAG) | алгоритм BM25 со стеммингом для казахского и русского |
| База данных | SQLite — история бесед и статистика |
| Тесты | pytest |
| Хостинг | VPS Namecheap (США), Linux Ubuntu |
| Запуск 24/7 | systemd: сервисы `adal-bot` и `adal-web`, автоперезапуск при сбое |

---

## 3. Основные зависимости и API

**Python-библиотеки** (`requirements.txt`):

| Библиотека | Для чего |
|---|---|
| `aiogram` | Telegram-бот |
| `fastapi`, `uvicorn` | веб-сервер и REST API |
| `python-multipart` | приём файлов в API |
| `openai` | клиент к AI-моделям (Claude и Groq поддерживают этот протокол) |
| `python-dotenv` | чтение ключей из `.env` |
| `PyYAML` | чтение базы знаний |
| `requests`, `beautifulsoup4` | загрузка официальных текстов законов |
| `pytest`, `httpx` | автотесты |

**Внешние API:**

| API | Для чего |
|---|---|
| Anthropic Claude API | формирование ответов |
| Groq API | распознавание голоса (Whisper) и резервная модель (Llama) |
| Telegram Bot API | работа бота |
| adilet.zan.kz | источник официальных текстов законов |

**API веб-версии:**

| Метод | Описание |
|---|---|
| `POST /api/chat` | вопрос → ответ бота с источниками |
| `POST /api/rate` | оценка ответа 👍/👎 |
| `GET /api/topics` | готовые темы |
| `GET /api/quiz` | вопрос викторины |
| `GET /api/stats?token=` | статистика (для администратора) |
| `GET /health` | проверка работы сервиса |

---

## 4. Архитектура проекта

```
 Пользователь (текст или голос, каз. / рус.)
          │
   Telegram-бот  /  Веб-интерфейс              ← Frontend
          │
   Фильтр: язык, пустые/длинные сообщения,     ← Backend (app/assistant.py)
           попытки взлома, «привет», «помощь»
          │
   Поиск по базе знаний (RAG)                  ← База знаний: kb/ (485 фрагментов)
          │
   AI-модель Claude: ответ строго               ← AI (резерв — Llama)
   по найденным фрагментам
          │
   Ответ + источники + пометка
   «закреплено в Конституции, статья N»
          │
   История и статистика                        ← Database (SQLite)
```

**Как обрабатывается вопрос:**
1. Сообщение приходит из Telegram или веб-версии; голос переводится в текст (Whisper).
2. Фильтр проверяет сообщение и определяет язык.
3. Поиск находит самые подходящие фрагменты официальных документов (законы разбиты по статьям).
4. Если ничего не найдено — бот честно отвечает, что подтверждённых данных нет.
5. Модель формирует ответ только по найденным фрагментам, со ссылками на них.
6. Код добавляет источники и пометку, если принцип закреплён в Конституции.
7. Вопрос и ответ сохраняются для памяти бесед и статистики.

**Структура файлов:**

| Файл | Назначение |
|---|---|
| `telegram_bot.py` | Telegram-бот: команды, голос, викторина |
| `web_app.py`, `web/index.html` | веб-версия и REST API |
| `app/assistant.py` | главная логика ответа (шаги 2–7) |
| `app/rag/loader.py`, `app/rag/retriever.py` | загрузка базы знаний и поиск |
| `app/llm.py`, `app/prompts.py` | работа с AI-моделями и правила ответа |
| `app/guard.py` | фильтр сообщений |
| `app/memory.py`, `app/analytics.py` | история бесед и статистика |
| `app/config.py` | настройки из `.env` |
| `kb/` | база знаний: Конституция, законы, концепции, Послания |
| `scripts/fetch_sources.py` | загрузка официальных текстов |
| `tests/` | автотесты |
