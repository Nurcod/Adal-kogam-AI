"""Предварительная проверка запроса до обращения к модели (быстро и бесплатно):
пустые/слишком длинные сообщения, приветствия, попытки «взломать» инструкции бота."""
import re

INJECTION_PATTERNS = [
    r"ignore (all |the )?(previous|above|prior) (instructions|prompts?)",
    r"system prompt", r"jailbreak", r"\bDAN\b", r"developer mode",
    r"игнорир\w*\s+(?:\w+\s+){0,3}(инструкц|правил|указан|промпт)",
    r"забудь\s+(?:\w+\s+){0,3}(инструкц|правил)", r"системн\w+ (промпт|инструкц)",
    r"ты теперь (не )?\w+ бот", r"притворись", r"представь, что ты",
    r"нұсқаулықт\w* елеме",
]
GREETINGS = {"привет", "здравствуйте", "салам", "сәлем", "сәлеметсіз бе", "салем", "hello", "hi",
             "добрый день", "қайырлы күн", "ассалаумағалейкум", "ассаламуалейкум"}

MAX_LEN = 1500


def precheck(text: str) -> str | None:
    """Возвращает код проблемы или None, если запрос можно обрабатывать."""
    clean = (text or "").strip()
    if not re.search(r"\w", clean):
        return "empty"
    if len(clean) > MAX_LEN:
        return "too_long"
    low = clean.lower()
    if any(re.search(p, low, re.IGNORECASE) for p in INJECTION_PATTERNS):
        return "injection"
    if low.strip("!.?, ") in GREETINGS:
        return "greeting"
    return None
