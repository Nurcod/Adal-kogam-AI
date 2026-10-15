"""Скачивает ПОЛНЫЕ официальные тексты документов с adilet.zan.kz и сохраняет их
в kb/official/ в формате базы знаний (с разбиением по статьям).

Запуск (нужен интернет):  python scripts/fetch_sources.py
После этого перезапустите бота — новые документы попадут в поиск автоматически.

Список документов — в scripts/sources.yaml. Добавить документ = добавить 4 строки.
"""
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "kb" / "official"
HEADERS = {"User-Agent": "Mozilla/5.0 (AdalKenesshi student project; educational)"}


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    # Берём блок, в котором больше всего упоминаний статей — это и есть текст акта
    best, best_n = soup.body or soup, 0
    for el in soup.find_all(["div", "article", "section", "td"]):
        txt = el.get_text(" ", strip=True)
        n = len(re.findall(r"Статья\s+\d+|\d+-бап", txt))
        if n > best_n or (n == best_n and n and len(txt) < len(best.get_text())):
            best, best_n = el, n
    text = best.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Каждую статью начинаем с новой строки, чтобы загрузчик разрезал по ним
    text = re.sub(r"\s*(Статья\s+\d+[\-.\d]*\.?)", r"\n\n\1", text)
    text = re.sub(r"\s*(\d+[\-.\d]*\s*-\s*бап\.?)", r"\n\n\1", text)
    return text.strip()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    docs = yaml.safe_load((ROOT / "scripts" / "sources.yaml").read_text(encoding="utf-8"))
    ok = 0
    for d in docs:
        print(f"→ {d['title']}")
        text = ""
        # Сначала официальный adilet, затем зеркала (adilet иногда отдаёт текст только через JS)
        for url in [d["url"]] + d.get("mirrors", []):
            try:
                r = requests.get(url, headers=HEADERS, timeout=40)
                r.raise_for_status()
                text = extract_text(r.text)
            except Exception as e:
                print(f"   ✗ {url}: {e}")
                continue
            if len(text) >= 2000:
                print(f"   ✓ загружено с {url}")
                break
            print(f"   ✗ {url}: мало текста ({len(text)} символов)")
        if len(text) < 2000:
            print("   ✗ не удалось получить текст — скачайте вручную и положите в kb/official/")
            continue
        meta = {
            "title": d["title"], "title_kk": d.get("title_kk", ""), "aliases": d.get("aliases", []),
            "sources": [f"{d['title']} | {d['url']}"],
            "in_constitution": d.get("in_constitution", ""),
            "split": "articles",
        }
        path = OUT / f"{d['id']}.md"
        path.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n" + text, encoding="utf-8")
        print(f"   ✓ {len(text):,} символов → {path.relative_to(ROOT)}")
        ok += 1
        time.sleep(1.5)  # бережно к серверу
    print(f"\nГотово: {ok}/{len(docs)} документов")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
