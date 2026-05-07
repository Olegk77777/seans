#!/usr/bin/env python3
"""
pdf_to_csv.py — парсер PDF-сборников поэзии в CSV-корпус.

Заточен под формат imwerden.de и подобных книжных PDF, где стихи разделены:
  * заголовком (ЗАГЛАВНЫМИ буквами) или маркером ***;
  * посвящением вида "В. С." после заголовка;
  * датой в конце ("1993, март" или "1993").

Результат — CSV с колонками author, title, text. Положи готовый файл в
   ~/Library/Application Support/АскБука Поэзии/data/corpus_<имя>.csv
и приложение само его подхватит при следующем запуске.

Использование:
   python3 pdf_to_csv.py <PDF-URL-или-путь> "Имя автора" [output.csv]

Примеры:
   python3 pdf_to_csv.py "https://imwerden.de/pdf/ryzhy_stikhi_2014__izd.pdf" \\
       "Борис Рыжий" \\
       "~/Library/Application Support/АскБука Поэзии/data/corpus_ryzhy.csv"

Зависимости: pymupdf  (pip3 install pymupdf)
"""

import csv
import re
import sys
import urllib.request
from pathlib import Path

DATE_RE = re.compile(r'^\s*(\d{4})(\s*,\s*[а-яё ]+)?\s*$')
SECTION_RE = re.compile(r'^\s*\d{4}\s*[–-]\s*\d{4}\s*$')
PAGE_RE = re.compile(r'^\s*\d{1,3}\s*$')
TITLE_RE = re.compile(r'^[А-ЯЁ\s\d.,!?\-—«»()\"\']+$')
DEDICATION_RE = re.compile(r'^[А-ЯЁ]\.\s*[А-ЯЁ]\.?\s*([А-ЯЁа-яё]+)?$')
# Маркеры стиха без названия. Помимо классических *** и ❖, в OCR-сканах
# часто ловятся артефакты вроде ^, <►, <\>, и микс из нескольких символов.
STAR_RE = re.compile(r'^[\x02\x03 *❖◊✦✶✻✲★☆·•⁂\^<>►◄♦◊\\\/]+$')
# Посвящение в новом сборнике может идти ПОСЛЕ даты,
# в свободной форме типа "Кейсу Верхейлу, с любовью" — короткая строка
POST_DEDICATION_RE = re.compile(
    r'^[А-ЯЁ][а-яё]+(\s+[А-ЯЁа-яё.]+)*[,]?\s*(с\s+\w+|памяти\s+\w+)?\s*$')


def fetch_pdf(src):
    """Скачивает PDF в /tmp если src — URL, иначе возвращает путь."""
    if src.startswith(('http://', 'https://')):
        out = Path('/tmp') / Path(src).name
        print(f'[1/3] Скачиваю {src} → {out}')
        req = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as r:
            out.write_bytes(r.read())
        return out
    return Path(src).expanduser()


def extract_text(pdf_path):
    print(f'[2/3] Извлекаю текст из {pdf_path}')
    import fitz
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text() for page in doc]
    return '\n'.join(pages)


def parse_poems(full_text):
    lines = [l.rstrip() for l in full_text.split('\n')]
    # Конец основного текста — "Содержание", "От составителя" и т.п.
    end = len(lines)
    for i, line in enumerate(lines):
        s = line.strip().upper()
        if i > 100 and (s == 'СОДЕРЖАНИЕ' or 'ОТ СОСТАВИТЕЛЯ' in s):
            end = i
            break
    # Начало — несколько эвристик: после "1993–1995" ИЛИ после первой даты
    # ИЛИ после первой строки чисто цифр (номер страницы) после преамбулы.
    start = 0
    for i, line in enumerate(lines[:600]):
        if SECTION_RE.match(line.strip()):
            start = i + 1
            break
    # Если разделов нет — берём первую дату стиха минус ~20 строк назад
    if start == 0:
        first_date_idx = None
        for i, line in enumerate(lines[:600]):
            if DATE_RE.match(line.strip()):
                first_date_idx = i
                break
        if first_date_idx is not None:
            # Откатываемся назад до первого PAGE_RE (номер страницы перед стихом)
            for j in range(first_date_idx, max(0, first_date_idx - 60), -1):
                if PAGE_RE.match(lines[j].strip()):
                    start = j + 1
                    break
            else:
                start = max(0, first_date_idx - 30)

    poems = []
    title = None
    dedication = None
    text_lines = []

    def flush():
        nonlocal title, dedication, text_lines
        if text_lines:
            text = '\n'.join(text_lines).strip()
            text = re.sub(r'\n{3,}', '\n\n', text)
            if not (text.count('\n') < 1 and len(text) < 30):
                t = title
                if not t or STAR_RE.match(t) or t == '*  *  *':
                    first = next((l for l in text_lines if l.strip()), '').strip()
                    t = first[:80] + ('...' if len(first) > 80 else '')
                # Title храним чистый (без посвящения) — для лучшего матчинга
                # с askbooka. Посвящение уйдёт в текст или будет потеряно.
                poems.append({'title': t.strip(), 'text': text})
        title = None
        dedication = None
        text_lines = []

    # after_date = True если только что закрылся стих по дате — несколько
    # следующих непустых строк могут быть посвящением к ТОЛЬКО ЧТО закрытому
    # стиху (формат имверденовского OCR-скана). Пропускаем их.
    after_date = False
    after_date_budget = 0

    for line in lines[start:end]:
        s = line.strip()
        if SECTION_RE.match(s):
            flush(); after_date = False; continue
        if DATE_RE.match(s) and text_lines:
            flush(); after_date = True; after_date_budget = 3; continue
        if PAGE_RE.match(s):
            # В некоторых сборниках стихи разделены ТОЛЬКО номером страницы
            # (без звёздочек/дат). Если уже есть текст и он заканчивается
            # пустой строкой — считаем, что стих закрылся.
            if text_lines and text_lines[-1] == '':
                flush()
            continue
        # После даты: ОЧЕНЬ короткая строка с именем-в-дательном — посвящение
        # к только что закрытому стиху. Длинные строки — это уже новый стих.
        if after_date and s and not STAR_RE.match(s):
            looks_like_dedication = (
                len(s) <= 50
                and POST_DEDICATION_RE.match(s)
                and ',' not in s.rstrip(',')[:-1]  # не запятая в середине
            )
            if after_date_budget > 0 and looks_like_dedication:
                after_date_budget -= 1
                continue
            after_date = False
        # Звёздочка-маркер: новый стих. Если уже есть текст — flush сначала.
        if STAR_RE.match(s):
            if text_lines:
                flush()
            title = '*  *  *'
            after_date = False
            continue
        if not s:
            if text_lines:
                text_lines.append('')
            continue
        # Заголовок (только если ещё нет)
        if not text_lines and not title and TITLE_RE.match(s) and 2 <= len(s) <= 80:
            if not re.match(r'^[IVX]+$', s):
                title = s
                after_date = False
                continue
        # Посвящение (классическое — после заголовка ДО стиха)
        if not text_lines and title and DEDICATION_RE.match(s):
            dedication = s
            continue
        text_lines.append(s)
        after_date = False
    flush()

    # Фильтр мусора
    def is_garbage(p):
        t = p['text']
        if re.search(r'\.{6,}\s*\d+', t):  # оглавление
            return True
        if 'ISBN' in t or 'Издание составил' in t or 'наследники' in t.lower()[:200]:
            return True
        return len(t) < 30

    return [p for p in poems if not is_garbage(p)]


def save_csv(poems, author, out_path):
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['author', 'title', 'text'])
        w.writeheader()
        for p in poems:
            w.writerow({'author': author, 'title': p['title'], 'text': p['text']})
    print(f'[3/3] Сохранено {len(poems)} стихов → {out_path}')


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    author = sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else f'corpus_{author.split()[-1].lower()}.csv'
    pdf = fetch_pdf(src)
    text = extract_text(pdf)
    poems = parse_poems(text)
    save_csv(poems, author, out)


if __name__ == '__main__':
    main()
