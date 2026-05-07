#!/usr/bin/env python3
"""
wikiquote_fetch.py — скачивает цитаты с ru.wikiquote.org для списка людей.

Викицитатник доступен из любой страны без VPN. Парсит шаблон {{Q|текст|Автор=...}},
чистит вики-разметку (ссылки, сноски, выделение). Результат — CSV с колонками
author, quote, source. Файл кладётся в data/-папку приложения, и оно его
автоматически подмешивает в базу как тип "quote".

Использование:
   python3 wikiquote_fetch.py <Имя_Страницы_В_Викицитатнике> [...]

Пример:
   python3 wikiquote_fetch.py "Виктор Цой" "Курт Кобейн" "Уинстон Черчилль"

Каждое имя — отдельный CSV-файл quotes_<slug>.csv в data/.
"""
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    'ASKBUKA_DATA_DIR',
    str(Path.home() / 'Library/Application Support/АскБука Поэзии/data')
))
UA = {'User-Agent': 'AskBukaPoetry/1.0 (https://github.com/oleg/askbuka; askbuka@local)'}


def fetch_wikitext(page_title):
    """Скачивает wikitext страницы Викицитатника. Возвращает текст или None."""
    url = 'https://ru.wikiquote.org/w/api.php?' + urllib.parse.urlencode({
        'action': 'parse', 'page': page_title, 'format': 'json',
        'prop': 'wikitext', 'redirects': '1',
    })
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode('utf-8'))
        return d.get('parse', {}).get('wikitext', {}).get('*', '')
    except Exception as e:
        print(f'  Ошибка скачивания {page_title}: {e}')
        return None


def split_template_params(s):
    """Разбивает строку шаблона {{Q|...}} на параметры по `|`,
    учитывая вложенные [[...]] и {{...}}."""
    parts = []
    cur = []
    depth_sq = 0
    depth_curly = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == '[' and i + 1 < len(s) and s[i+1] == '[':
            depth_sq += 1
            cur.append('[['); i += 2
            continue
        if c == ']' and i + 1 < len(s) and s[i+1] == ']':
            depth_sq = max(0, depth_sq - 1)
            cur.append(']]'); i += 2
            continue
        if c == '{' and i + 1 < len(s) and s[i+1] == '{':
            depth_curly += 1
            cur.append('{{'); i += 2
            continue
        if c == '}' and i + 1 < len(s) and s[i+1] == '}':
            depth_curly = max(0, depth_curly - 1)
            cur.append('}}'); i += 2
            continue
        if c == '|' and depth_sq == 0 and depth_curly == 0:
            parts.append(''.join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    parts.append(''.join(cur))
    return parts


def clean_wikitext(text):
    """Убирает вики-разметку: ссылки, сноски, шаблоны, выделение."""
    if not text:
        return ''
    # Сноски <ref>...</ref> и <ref name="..."/>
    text = re.sub(r'<ref[^>]*?/>', '', text)
    text = re.sub(r'<ref[^>]*?>.*?</ref>', '', text, flags=re.DOTALL)
    # Прочие HTML-теги
    text = re.sub(r'<[^>]+>', '', text)
    # Шаблоны типа {{rp|с.248}}, {{comment|x|y}} — убираем
    while '{{' in text:
        new = re.sub(r'\{\{[^{}]*\}\}', '', text)
        if new == text:
            break
        text = new
    # Ссылки: [[link|text]] → text, [[link]] → link
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    # Жирный/курсив: ''text'' → text, '''text''' → text
    text = re.sub(r"'''+(.+?)'''+", r'\1', text)
    text = re.sub(r"''(.+?)''", r'\1', text)
    # Внешние ссылки [http://... text] → text
    text = re.sub(r'\[https?://\S+\s+([^\]]+)\]', r'\1', text)
    text = re.sub(r'\[https?://\S+\]', '', text)
    # Лишние пробелы
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_quotes(wikitext, person_name):
    """Парсит wikitext страницы Викицитатника, возвращает список цитат
    САМОГО ЧЕЛОВЕКА (не «о нём»).

    Стратегия: обрезаем wikitext до первого «нежелательного» раздела
    (== Цитаты о ... ==, == Литература ==, == Ссылки == и т.п.),
    потом ищем все {{Q|...}} в оставшемся.
    """
    if not wikitext:
        return []

    # Маркеры конца «полезной» части — ловим ==-заголовки 2-го/3-го уровня
    # с типичными нежелательными словами в начале.
    bad_section_re = re.compile(
        r'^(==+)\s*('
        r'цитат[аы]?\s+о\s+|'
        r'о\s+(нём|нем|ней|жизни)|'
        r'воспоминани|'
        r'литератур|'
        r'источник|'
        r'примечани|'
        r'ссылк|'
        r'см\.?\s+также|'
        r'библиограф|'
        r'диалоги\s+о|'
        r'биограф'
        r')',
        re.IGNORECASE | re.MULTILINE)
    m = bad_section_re.search(wikitext)
    if m:
        wikitext = wikitext[:m.start()]

    quotes = []
    # Викитека использует разные шаблоны: {{Q|...}}, {{Цитата|Цитата=...|Автор=...}},
    # {{ЦитатаД|...}} и т.п. Ищем все шаблоны, имя которых начинается с "Q" или
    # "Цитата" / "Quote".
    template_starts = ['{{Q|', '{{Цитата|', '{{цитата|', '{{Quote|', '{{quote|']
    positions = []
    for marker in template_starts:
        i = 0
        while True:
            p = wikitext.find(marker, i)
            if p == -1:
                break
            positions.append((p, len(marker)))
            i = p + 1
    positions.sort()

    seen_starts = set()
    for start, marker_len in positions:
        if start in seen_starts:
            continue
        seen_starts.add(start)
        depth = 1
        j = start + marker_len
        while j < len(wikitext) and depth > 0:
            if wikitext[j:j+2] == '{{':
                depth += 1; j += 2
            elif wikitext[j:j+2] == '}}':
                depth -= 1; j += 2
            else:
                j += 1
        if depth != 0:
            continue
        body = wikitext[start+marker_len:j-2]
        params = split_template_params(body)
        named = {}
        positional = []
        for p in params:
            if '=' in p and re.match(r'^\s*[А-Яа-яA-Za-z][\w\s]*=', p):
                k, v = p.split('=', 1)
                named[k.strip()] = v.strip()
            else:
                positional.append(p)
        # Текст цитаты: либо именованный Цитата=, либо первый позиционный
        quote_raw = (named.get('Цитата') or named.get('цитата') or
                     named.get('Quote') or named.get('quote') or
                     (positional[0] if positional else ''))
        quote = clean_wikitext(quote_raw)
        source = clean_wikitext(named.get('Автор', '') or named.get('автор', '') or
                                 (positional[1] if len(positional) > 1 else ''))
        if quote and len(quote) >= 8:
            if not re.fullmatch(r'[\d\s.,«»\-]+', quote):
                quotes.append({'quote': quote, 'source': source})

    return quotes


def slugify(name):
    s = name.lower().replace(' ', '_')
    s = re.sub(r'[^a-zа-яё0-9_-]+', '', s)
    return s


def fetch_and_save(person_name):
    print(f'\n→ {person_name}')
    page = person_name.replace(' ', '_')
    wt = fetch_wikitext(page)
    if not wt:
        print(f'  страница не найдена')
        return 0
    quotes = extract_quotes(wt, person_name)
    if not quotes:
        print(f'  цитаты не извлечены')
        return 0
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f'quotes_{slugify(person_name)}.csv'
    with open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['author', 'quote', 'source'])
        w.writeheader()
        for q in quotes:
            w.writerow({'author': person_name, 'quote': q['quote'],
                        'source': q['source']})
    print(f'  ✓ {len(quotes)} цитат → {out.name}')
    return len(quotes)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    total = 0
    for name in sys.argv[1:]:
        total += fetch_and_save(name)
        time.sleep(0.5)  # вежливо к Wikimedia API
    print(f'\nИтого скачано цитат: {total}')


if __name__ == '__main__':
    main()
