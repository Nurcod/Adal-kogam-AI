"""Загрузка базы знаний: markdown-файлы с YAML-шапкой -> фрагменты (chunks).

Формат файла в kb/:
---
title: Закон и Порядок
title_kk: Заң және тәртіп
aliases: [закон и порядок, law and order]
sources:
  - Конституция РК 2026 | https://adilet.zan.kz/...
in_constitution: "Преамбула Конституции РК 2026"   # пусто, если не закреплено
split: articles     # необязательно: резать текст по статьям («Статья N» / «N-бап»)
---
Текст...
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CHUNK_CHARS = 900
OVERLAP_CHARS = 150

ARTICLE_RE = re.compile(r"(?m)^\s*(?:Статья\s+\d+[\-.\d]*|\d+[\-.\d]*\s*-\s*бап)")


@dataclass
class Source:
    name: str
    url: str


@dataclass
class Chunk:
    id: int
    doc_id: str
    title: str
    text: str
    sources: list = field(default_factory=list)
    in_constitution: str = ""
    keywords: str = ""  # title_kk + aliases: помогают находить документ на обоих языках
    label: str = ""     # например «Статья 3»


def _parse_file(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        _, head, body = raw.split("---", 2)
        meta = yaml.safe_load(head) or {}
    else:
        meta, body = {}, raw
    return meta, body.strip()


def _parse_sources(items) -> list[Source]:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(Source(item.get("name", ""), item.get("url", "")))
        elif "|" in str(item):
            name, url = str(item).rsplit("|", 1)
            out.append(Source(name.strip(), url.strip()))
        else:
            out.append(Source(str(item).strip(), ""))
    return out


def _split_paragraphs(body: str) -> list[tuple[str, str]]:
    """Режем по абзацам, склеивая их до ~CHUNK_CHARS. Заголовок секции дописываем к фрагменту."""
    pieces, buf, heading = [], "", ""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            heading = para.lstrip("# ").split("\n")[0]
        if len(buf) + len(para) > CHUNK_CHARS and buf:
            pieces.append((heading, buf))
            buf = buf[-OVERLAP_CHARS:] + "\n\n" + para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        pieces.append((heading, buf))
    return pieces


def _split_articles(body: str) -> list[tuple[str, str]]:
    starts = [m.start() for m in ARTICLE_RE.finditer(body)]
    if not starts:
        return _split_paragraphs(body)
    pieces = []
    if starts[0] > 0:
        pieces.append(("Преамбула", body[: starts[0]].strip()))
    for i, s in enumerate(starts):
        part = body[s: starts[i + 1] if i + 1 < len(starts) else len(body)].strip()
        label = ARTICLE_RE.match(part).group().strip(" .\n")  # «Статья 53» / «53-бап»
        # длинные статьи дорезаем
        if len(part) > CHUNK_CHARS * 2:
            for _, sub in _split_paragraphs(part):
                pieces.append((label, sub if sub.startswith(label) else f"{label}\n{sub}"))
        else:
            pieces.append((label, part))
    return pieces


def load_kb(kb_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.rglob("*.md")):
        if path.name.upper().startswith("README"):
            continue
        meta, body = _parse_file(path)
        if not body:
            continue
        title = meta.get("title") or path.stem
        aliases = meta.get("aliases") or []
        keywords = " ".join([title, meta.get("title_kk", "")] + [str(a) for a in aliases])
        splitter = _split_articles if meta.get("split") == "articles" else _split_paragraphs
        for label, text in splitter(body):
            chunks.append(Chunk(
                id=len(chunks),
                doc_id=str(path.relative_to(kb_dir)),
                title=title,
                text=text,
                sources=_parse_sources(meta.get("sources")),
                # «{label}» в шапке подставляется номером статьи: «Статья 43»
                in_constitution=str(meta.get("in_constitution") or "").strip().replace("{label}", label or "текст Конституции"),
                keywords=keywords,
                label=label if meta.get("split") == "articles" else "",
            ))
    return chunks
