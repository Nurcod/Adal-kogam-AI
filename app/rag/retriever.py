"""Поиск по базе знаний.

1. BM25 — классический лексический поиск (работает без интернета и без ключей).
   Для русского и казахского используем упрощённый стемминг: обрезаем слово до первых
   5 букв («конституции», «конституцией» -> «конст»), этого достаточно для морфологии.
2. Эмбеддинги (необязательно) — семантический поиск через API, кэшируются в data/.
3. Результаты объединяются методом Reciprocal Rank Fusion (RRF).
"""
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from app.config import settings
from app.rag.loader import Chunk

STOPWORDS = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было
вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь
опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам
чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь
этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой
хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою
этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это расскажи скажи
что-то такое такое значит означает подробнее пожалуйста объясни
және мен бен пен да де та те ма ме ба бе па пе ғой қой бұл осы сол ол не неге қалай қандай қай кім
нені туралы үшін бар жоқ еді екен деген айтып айтшы түсіндір түсіндіріңіз маған бізге сіз сен
""".split())

TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)


def stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("ё", "е")
    out = []
    for tok in TOKEN_RE.findall(text):
        tok = tok.strip("-_")
        if len(tok) < 2 or tok in STOPWORDS:
            continue
        out.append(stem(tok))
    return out


@dataclass
class Hit:
    chunk: Chunk
    score: float      # итоговый балл ранжирования (RRF)
    coverage: float   # доля слов запроса, найденных во фрагменте (0..1)
    cosine: float = 0.0


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avgdl = sum(self.len) / max(len(docs), 1)
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        res = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            res.append(s)
        return res


class Retriever:
    def __init__(self, chunks: list[Chunk], embedder=None):
        self.chunks = chunks
        # Заголовок и ключевые слова (в т.ч. казахское название) добавляем дважды — буст по названию
        self.doc_tokens = [tokenize(f"{c.keywords} {c.keywords} {c.label} {c.text}") for c in chunks]
        self.doc_sets = [set(t) for t in self.doc_tokens]
        self.bm25 = BM25(self.doc_tokens)
        self.embedder = embedder
        self.vectors = None
        if embedder is not None:
            self.vectors = self._load_or_build_vectors()

    # ---------- эмбеддинги ----------
    def _load_or_build_vectors(self):
        texts = [f"{c.title}. {c.label}\n{c.text}" for c in self.chunks]
        digest = hashlib.md5(("\n".join(texts) + settings.emb_model).encode()).hexdigest()[:12]
        cache = settings.data_dir / f"emb_{digest}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        vecs = []
        for i in range(0, len(texts), 64):
            vecs.extend(self.embedder(texts[i:i + 64]))
        cache.write_text(json.dumps(vecs))
        return vecs

    @staticmethod
    def _cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    # ---------- поиск ----------
    def search(self, queries: list[str], k: int = 5) -> list[Hit]:
        """Принимает несколько формулировок запроса (оригинал + переформулировка LLM)."""
        rrf: dict[int, float] = {}
        coverage: dict[int, float] = {}
        cosine: dict[int, float] = {}

        for q in [q for q in queries if q and q.strip()]:
            q_tokens = tokenize(q)
            q_set = set(q_tokens)
            if q_tokens:
                bm = self.bm25.scores(q_tokens)
                ranked = sorted(range(len(bm)), key=lambda i: bm[i], reverse=True)
                for rank, i in enumerate(ranked[:30]):
                    if bm[i] <= 0:
                        break
                    rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
                    cov = len(q_set & self.doc_sets[i]) / len(q_set)
                    coverage[i] = max(coverage.get(i, 0), cov)
            if self.vectors is not None:
                try:
                    qv = self.embedder([q])[0]
                    sims = [self._cos(qv, v) for v in self.vectors]
                    ranked = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
                    for rank, i in enumerate(ranked[:30]):
                        rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
                        cosine[i] = max(cosine.get(i, 0), sims[i])
                except Exception:  # эмбеддинги — не критичны, BM25 продолжит работать
                    pass

        hits = [Hit(self.chunks[i], s, coverage.get(i, 0.0), cosine.get(i, 0.0)) for i, s in rrf.items()]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
