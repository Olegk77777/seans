#!/usr/bin/env python3
"""
wayback_drain.py — массовое скачивание всех непокрытых стихов из Wayback.

Для каждого стиха в базе без поля text:
  - идём на Wayback Machine за askbooka URL,
  - извлекаем текст стиха через BeautifulSoup,
  - инкрементально пишем в poems.json + corpus_wayback.csv,
  - терпим rate-limit от Wayback (медленные паузы, ретраи).

Логирует в /tmp/wayback_drain.log.
"""
import sys, csv, re, time, urllib.request, urllib.error, json
from pathlib import Path

sys.path.insert(0, '/Users/olegkrugliak/Codex/AskBukaPoetry')
import os
os.environ['ASKBUKA_DATA_DIR'] = '/Users/olegkrugliak/Library/Application Support/АскБука Поэзии/data'
import app
from bs4 import BeautifulSoup

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
LOG = '/tmp/wayback_drain.log'
EXTRA_CSV = Path(os.environ['ASKBUKA_DATA_DIR']) / 'corpus_wayback.csv'

SLEEP_OK = 2.5      # пауза после успеха
SLEEP_FAIL = 6.0    # пауза после connection refused
SLEEP_PENALTY = 60  # если 5 подряд connection refused — большая пауза


def log(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def fetch_text(url, attempt=1, max_attempts=3):
    wb = f'https://web.archive.org/web/0/{url}'
    try:
        req = urllib.request.Request(wb, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, '404'
        if attempt < max_attempts:
            time.sleep(8 * attempt)
            return fetch_text(url, attempt + 1, max_attempts)
        return None, f'HTTP{e.code}'
    except Exception as e:
        if attempt < max_attempts:
            time.sleep(8 * attempt)
            return fetch_text(url, attempt + 1, max_attempts)
        return None, type(e).__name__
    soup = BeautifulSoup(html, 'lxml')
    body = (soup.select_one('.field-name-field-stih .field-item')
            or soup.select_one('.field-name-body .field-item')
            or soup.select_one('article .content'))
    if body:
        text = body.get_text("\n").strip()
        text = re.sub(r'\s*→→→\s*', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        if len(text) > 30:
            return text[:20000], None
    return None, 'no-body'


def main():
    data = app.load_data()
    missing = [it for it in data if it.get('link') and not it.get('text')
               and it.get('type') in ('poem', 'bibliography')]
    log(f'СТАРТ. Нужно скачать: {len(missing)}')

    # Загружаем уже скачанные через Wayback (если перезапуск)
    existing = {}
    if EXTRA_CSV.exists():
        with open(EXTRA_CSV, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                existing[(row['author'], row['title'])] = row

    ok = 0
    fail = 0
    fail_streak = 0
    last_save = time.time()

    for i, it in enumerate(missing, 1):
        # пропускаем уже добытое (если перезапуск)
        key = (it['author'], it['title'])
        if key in existing:
            it['text'] = existing[key]['text']
            continue

        text, err = fetch_text(it['link'])
        if text:
            ok += 1
            fail_streak = 0
            it['text'] = text
            existing[key] = {'author': it['author'], 'title': it['title'], 'text': text}
            log(f'  {i:4d}/{len(missing)} ✓ {it["author"][:18]:18s} — {it["title"][:35]}')
            time.sleep(SLEEP_OK)
        else:
            fail += 1
            if err.startswith(('URLError', 'ConnectionError')):
                fail_streak += 1
            else:
                fail_streak = 0
            log(f'  {i:4d}/{len(missing)} ✗ {it["author"][:18]:18s} — {it["title"][:35]} ({err})')
            if fail_streak >= 5:
                log(f'  >>> {fail_streak} подряд ошибок — пауза {SLEEP_PENALTY} сек')
                time.sleep(SLEEP_PENALTY)
                fail_streak = 0
            else:
                time.sleep(SLEEP_FAIL)

        # Инкрементальное сохранение каждые 30 сек
        if time.time() - last_save > 30:
            with open(EXTRA_CSV, 'w', encoding='utf-8', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['author', 'title', 'text'])
                w.writeheader()
                w.writerows(existing.values())
            app.save_data(data)
            with_text = sum(1 for x in data if x.get('text'))
            log(f'  >>> SAVE: всего {with_text}/{len(data)} с текстом '
                f'· в этом проходе ✓{ok} ✗{fail}')
            last_save = time.time()

    # Финальный save
    with open(EXTRA_CSV, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['author', 'title', 'text'])
        w.writeheader()
        w.writerows(existing.values())
    app.save_data(data)
    with_text = sum(1 for x in data if x.get('text'))
    log(f'ГОТОВО: ✓{ok} ✗{fail}. Всего с текстом: {with_text}/{len(data)}')


if __name__ == '__main__':
    main()
