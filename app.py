#!/usr/bin/env python3
"""
Сеанс — литературно-визуальная рулетка
======================================
Локальное macOS-приложение: рулетка из стихов, цитат и кинокадров.

Главная — рулетка из отрывков и цитат (фильтры по категориям, авторам).
Библиотека — каталог поэтов с поиском.
Кадры — визуальная рулетка стоп-кадров из кино + библиотека.

Источники полностью автономны: локальные CSV-корпуса (HuggingFace,
сборники из imwerden), Wikiquote, Wayback Machine, askbooka.ru как
исторический seed-каталог.

Запуск: python3 app.py
"""

import http.server
import json
import os
import re
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

# ---------- НАСТРОЙКИ ----------
PORT = int(os.environ.get('SEANS_PORT') or os.environ.get('PORT') or 8765)
# Поддерживаем и старую ASKBUKA_DATA_DIR (для обратной совместимости с
# существующими копиями), и новую SEANS_DATA_DIR. Приоритет — у новой.
DATA_DIR = Path(
    os.environ.get("SEANS_DATA_DIR")
    or os.environ.get("ASKBUKA_DATA_DIR")
    or (Path(__file__).parent / "data")
)
DATA_FILE = DATA_DIR / "poems.json"
CORPUS_DIR = DATA_DIR  # все CSV-корпуса хранятся прямо в data/
# Несколько открытых корпусов с HuggingFace (доступны без VPN из любой страны).
# Скачиваются по одному файлу; объединённый индекс держится в памяти.
# Колонки: author, name, text  (формат разный — нормализуем при загрузке).
CORPUS_SOURCES = [
    {
        "id": "georgii",
        "title": "Русская поэзия (Georgii)",
        "url": "https://huggingface.co/datasets/Georgii/russianPoetry/resolve/main/russianPoetryWithTheme.csv",
        "filename": "corpus.csv",  # старое имя — сохраняем для уже скачанных
        "fields": {"author": "author", "title": "name", "text": "text"},
    },
    {
        "id": "brodsky",
        "title": "Иосиф Бродский",
        "url": "https://huggingface.co/datasets/vicemik/brodsky-poetry/resolve/main/brod_poems_with_titles.csv",
        "filename": "corpus_brodsky.csv",
        "fields": {"author": "author", "title": "title", "text": "text"},
        # У Бродского в этом датасете author = просто "Бродский"; нормализуем
        "default_author": "Иосиф Бродский",
    },
]
# Старая константа — для обратной совместимости. Старое имя файла.
CORPUS_FILE = DATA_DIR / "corpus.csv"
CORPUS_URL = CORPUS_SOURCES[0]["url"]
BASE_URL = "https://www.askbooka.ru"
BASE_HOST = "www.askbooka.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
# Wikimedia API теперь требует осмысленный User-Agent с контактом — иначе 403.
# https://meta.wikimedia.org/wiki/User-Agent_policy
WIKIMEDIA_HEADERS = {
    "User-Agent": "AskBukaPoetry/1.0 (https://github.com/oleg/askbuka-poetry; askbuka-app@local)"
}

# Пагинация Drupal: ?page=1,2,...
MAX_PAGES_PER_THEME = 5
MAX_PAGES_PER_AUTHOR = 15  # у классиков (Пушкин) бывает много страниц
REQUEST_PAUSE = 0.15  # пауза между sequential-запросами
AUTHOR_WORKERS = 5   # параллелизм при обходе авторов
POEM_FETCH_TIMEOUT = 30  # таймаут при подгрузке полного текста стиха
POEM_FETCH_RETRIES = 2   # сколько раз повторить при сетевой ошибке

# Категории — попадают в фильтры на «Библиотеке»
FEATURED_CATEGORIES = {"Топ-100 стихов", "Топ-100 цитат", "Любимые стихи"}

# Полный список из 100 тем сайта.
THEMES = [
    ("avgust", "Август"), ("aprel", "Апрель"), ("bessonnica", "Бессонница"),
    ("bolnica", "Больница"), ("burya", "Буря"), ("v-zaklyuchenii", "В заключении"),
    ("v-restorane", "В ресторане"), ("vesna", "Весна"), ("veter", "Ветер"),
    ("vecher", "Вечер"), ("voyna", "Война"), ("vospominaniya", "Воспоминания"),
    ("vremya", "Время"), ("goroda", "Города"), ("gory", "Горы"),
    ("groza", "Гроза"), ("derevnya", "Деревня"), ("derevya", "Деревья"),
    ("deti", "Дети"), ("dialog", "Диалог"), ("dozhd", "Дождь"),
    ("doroga", "Дорога"), ("druzya", "Друзья"), ("dusha", "Душа"),
    ("est-takaya-pesnya", "Есть такая песня"), ("zheleznaya-doroga", "Железная дорога"),
    ("zhenshchina", "Женщина"), ("zhivotnye", "Животные"), ("zhizn", "Жизнь"),
    ("zakat", "Закат"), ("zvyozdy", "Звёзды"), ("zima", "Зима"),
    ("znakomye-vsyo-lica", "Знакомые всё лица"), ("istina-v-vine", "Истина в вине"),
    ("knigi", "Книги"), ("korabli", "Корабли"), ("koshki", "Кошки"),
    ("les", "Лес"), ("leto", "Лето"), ("listya", "Листья"),
    ("luna", "Луна"), ("lyubov", "Любовь"), ("mart", "Март"),
    ("mgnovenie", "Мгновение"), ("metel", "Метель"), ("more", "Море"),
    ("moskva", "Москва"), ("muza", "Муза"), ("muzyka", "Музыка"),
    ("my", "Мы"), ("nebo", "Небо"), ("novyy-god", "Новый год"),
    ("noch", "Ночь"), ("o-stranah", "О странах"), ("oblaka", "Облака"),
    ("ogon", "Огонь"), ("odinochestvo", "Одиночество"), ("oktyabr", "Октябрь"),
    ("osen", "Осень"), ("pamyatnik", "Памятник"), ("parizh", "Париж"),
    ("park", "Парк"), ("personazhi-knig", "Персонажи книг"),
    ("peterburg", "Петербург"), ("pisma", "Письма"),
    ("posvyashchenie-poetu", "Посвящение поэту"), ("potomkam", "Потомкам"),
    ("poety-i-poeziya", "Поэты и поэзия"), ("priroda", "Природа"),
    ("proshchanie", "Прощание"), ("pticy", "Птицы"), ("rassvet", "Рассвет"),
    ("reka", "Река"), ("rossiya", "Россия"), ("russkiy-yazyk", "Русский язык"),
    ("sad", "Сад"), ("sentyabr", "Сентябрь"), ("skuka", "Скука"),
    ("slava", "Слава"), ("slova", "Слова"), ("sneg", "Снег"),
    ("smert", "Смерть"), ("sny", "Сны"), ("sobaki", "Собаки"), ("solnce", "Солнце"),
    ("step", "Степь"), ("studenty", "Студенты"), ("schaste", "Счастье"),
    ("teatr", "Театр"), ("telefon", "Телефон"), ("toska", "Тоска"),
    ("u-okna", "У окна"), ("ulica", "Улица"), ("utro", "Утро"),
    ("fevral", "Февраль"), ("cvety", "Цветы"), ("shutlivye-stihi", "Шутливые стихи"),
    ("emigrantskoe", "Эмигрантское"), ("epigramma", "Эпиграмма"), ("yanvar", "Январь"),
]

# Разделы цитат
QUOTE_SECTIONS = [
    ("quotations-top.html", "Топ-100 цитат"),
    ("quotations/stihi.html", "Поэзия — цитаты"),
]

# Прогресс парсинга
PROGRESS = {
    "running": False, "total_steps": 0, "step": 0, "stage": "",
    "items": 0, "ok": None, "error": None,
}
PROGRESS_LOCK = threading.Lock()

# Событие отмены. set() — просят остановиться; clear() — новый старт.
CANCEL_EVENT = threading.Event()


def progress_set(**kwargs):
    with PROGRESS_LOCK:
        PROGRESS.update(kwargs)


def progress_get():
    with PROGRESS_LOCK:
        return dict(PROGRESS)


# ---------- ПАРСЕР ----------
def clean_text(text):
    text = re.sub(r'\s*→→→\s*', '', text)
    text = text.strip()
    lines = [l.strip() for l in text.split('\n')]
    return '\n'.join(l for l in lines if l)


def parse_author_title(full_title):
    match = re.match(r'(.+?)\s*[«""](.+?)[»""]', full_title)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return full_title, ""


def _fetch_listing_page(url, session, parse_row):
    from bs4 import BeautifulSoup
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
    except Exception:
        return None  # сетевая ошибка — сигнал «прекращай пагинацию»
    r.encoding = 'utf-8'
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, 'lxml')
    items = []
    for li in soup.select('li[class*="views-row"]'):
        item = parse_row(li)
        if item:
            items.append(item)
    return items


def _parse_poem_row(li):
    title_el = li.select_one('.views-field-title .field-content a')
    text_el = li.select_one('.views-field-field-stih .field-content')
    if not (title_el and text_el):
        return None
    author, poem_title = parse_author_title(title_el.get_text(strip=True))
    excerpt = clean_text(text_el.get_text())
    if not excerpt or len(excerpt) < 20:
        return None
    link = title_el.get('href', '')
    if link and not link.startswith('http'):
        link = BASE_URL + link
    return {"author": author, "title": poem_title, "excerpt": excerpt,
            "link": link, "type": "poem"}


def _parse_quote_row(li):
    text_el = li.select_one('.views-field-field-citat .field-content')
    source_el = li.select_one('.views-field-field-name-book .field-content a')
    if not (text_el and source_el):
        return None
    author, book = parse_author_title(source_el.get_text(strip=True))
    excerpt = clean_text(text_el.get_text())
    if not excerpt or len(excerpt) < 20:
        return None
    link = source_el.get('href', '')
    if link and not link.startswith('http'):
        link = BASE_URL + link
    return {"author": author, "title": book, "excerpt": excerpt,
            "link": link, "type": "quote"}


def _fetch_with_pagination(base_url, parser, session, max_pages=MAX_PAGES_PER_THEME):
    all_items = []
    seen_first = None
    pages_to_try = [base_url] + [f"{base_url}?page={p}" for p in range(1, max_pages + 1)]
    for url in pages_to_try:
        items = _fetch_listing_page(url, session, parser)
        if not items:
            break
        first_key = items[0].get("excerpt", items[0].get("title", ""))[:60]
        if first_key and first_key == seen_first:
            break
        seen_first = first_key
        all_items.extend(items)
        time.sleep(REQUEST_PAUSE)
    return all_items


# ----- АВТОРЫ И КАТАЛОГ -----
def _slug_from_link(link):
    """Извлекает slug автора из ссылки на стих:
    https://www.askbooka.ru/stihi/aleksandr-pushkin/lyubvi-vse-vozrasty.html
    → aleksandr-pushkin
    """
    if not link:
        return None
    p = urlparse(link).path  # /stihi/aleksandr-pushkin/lyubvi-vse-vozrasty.html
    parts = [x for x in p.split('/') if x]
    if len(parts) >= 2 and parts[0] == 'stihi':
        return parts[1]
    return None


def _fetch_author_page(url, author_name, author_slug, session):
    """Парсит страницу автора, возвращает список словарей-стихов."""
    from bs4 import BeautifulSoup
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
    except Exception:
        return None
    r.encoding = 'utf-8'
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, 'lxml')
    items = []
    seen = set()
    # Ищем все ссылки вида /stihi/<author-slug>/<smth>.html
    needle = f"/stihi/{author_slug}/"
    for a in soup.select('a[href]'):
        href = a.get('href', '')
        if needle not in href or not href.endswith('.html'):
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 2:
            continue
        full_link = href if href.startswith('http') else BASE_URL + href
        if full_link in seen:
            continue
        seen.add(full_link)
        items.append({
            "author": author_name, "title": title, "link": full_link,
            "type": "bibliography", "category": "Каталог",
        })
    return items


def _crawl_author(slug, name, session):
    """Все стихи автора с пагинацией."""
    base = f"{BASE_URL}/stihi/{slug}"
    all_items = []
    seen_link = None
    for page in [None] + list(range(1, MAX_PAGES_PER_AUTHOR + 1)):
        url = base if page is None else f"{base}?page={page}"
        items = _fetch_author_page(url, name, slug, session)
        if not items:
            break
        if items[0]["link"] == seen_link:
            break
        seen_link = items[0]["link"]
        all_items.extend(items)
        time.sleep(REQUEST_PAUSE)
    return all_items


def scrape_all():
    """Главная функция парсинга: отрывки + полный каталог по авторам.

    Устойчива к обрывам: сохраняет `poems.json` после каждой стадии и
    каждые 10 завершённых авторов / каждые 30 успешно скачанных текстов.
    Если нажата «Остановить» (CANCEL_EVENT) — выходим, сохраняя что есть.
    """
    CANCEL_EVENT.clear()
    try:
        import requests
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        raise ImportError("Нужны: pip3 install requests beautifulsoup4 lxml")

    session = requests.Session()
    excerpts = []  # отрывки с текстом — Главная (рулетка)

    # Стадия 1: отрывки (Топ + Любимые + темы + цитаты)
    base_steps = 1 + 1 + len(THEMES) + len(QUOTE_SECTIONS)
    progress_set(running=True, total_steps=base_steps, step=0,
                 stage="Запускаю...", items=0, ok=None, error=None)

    def add_excerpts(items, category):
        for it in items:
            it["category"] = category
        excerpts.extend(items)
        progress_set(items=len(excerpts))

    step = 0

    step += 1; progress_set(step=step, stage="Топ-100 стихов")
    add_excerpts(_fetch_with_pagination(f"{BASE_URL}/stihi-top100.html",
                                        _parse_poem_row, session, max_pages=2),
                 "Топ-100 стихов")

    step += 1; progress_set(step=step, stage="Любимые стихи читателей")
    add_excerpts(_fetch_with_pagination(f"{BASE_URL}/love-poetry/top.html",
                                        _parse_poem_row, session, max_pages=2),
                 "Любимые стихи")

    for slug, name in THEMES:
        if CANCEL_EVENT.is_set():
            raise InterruptedError("cancelled")
        step += 1; progress_set(step=step, stage=f"Тема: {name}")
        url = f"{BASE_URL}/stihi/tema/{slug}.html"
        add_excerpts(_fetch_with_pagination(url, _parse_poem_row, session), name)

    for path, name in QUOTE_SECTIONS:
        if CANCEL_EVENT.is_set():
            raise InterruptedError("cancelled")
        step += 1; progress_set(step=step, stage=name)
        url = f"{BASE_URL}/{path}"
        add_excerpts(_fetch_with_pagination(url, _parse_quote_row, session), name)

    # Дедуп отрывков
    by_excerpt = {}
    for item in excerpts:
        key = item["excerpt"][:80]
        existing = by_excerpt.get(key)
        if existing is None:
            by_excerpt[key] = item
        elif item.get("category") in FEATURED_CATEGORIES and \
                existing.get("category") not in FEATURED_CATEGORIES:
            by_excerpt[key] = item
    excerpts = list(by_excerpt.values())
    for it in excerpts:
        it["featured"] = it.get("category") in FEATURED_CATEGORIES

    # Стадия 2: каталог по авторам
    # Собираем список авторов из ссылок отрывков
    authors_map = {}  # slug -> author_name
    for it in excerpts:
        slug = _slug_from_link(it.get("link", ""))
        if slug and it.get("author"):
            # для одного slug может приходить разное имя — берём первое
            authors_map.setdefault(slug, it["author"])

    # Добавим ещё кучу классических авторов, которые могут не появиться
    # в отрывках (они и так есть на сайте)
    EXTRA_AUTHORS = [
        ("aleksandr-pushkin", "Александр Пушкин"),
        ("mihail-lermontov", "Михаил Лермонтов"),
        ("nikolay-nekrasov", "Николай Некрасов"),
        ("fyodor-tyutchev", "Фёдор Тютчев"),
        ("afanasiy-fet", "Афанасий Фет"),
        ("sergey-esenin", "Сергей Есенин"),
        ("anna-ahmatova", "Анна Ахматова"),
        ("marina-cvetaeva", "Марина Цветаева"),
        ("boris-pasternak", "Борис Пастернак"),
        ("aleksandr-blok", "Александр Блок"),
        ("iosif-brodskiy", "Иосиф Бродский"),
        ("vladimir-mayakovskiy", "Владимир Маяковский"),
        ("nikolay-gumilyov", "Николай Гумилёв"),
        ("osip-mandelshtam", "Осип Мандельштам"),
        ("ivan-bunin", "Иван Бунин"),
        ("evgeniy-evtushenko", "Евгений Евтушенко"),
        ("bulat-okudzhava", "Булат Окуджава"),
        ("vladimir-vysockiy", "Владимир Высоцкий"),
        ("eduard-asadov", "Эдуард Асадов"),
        ("samuil-marshak", "Самуил Маршак"),
        ("kornei-chukovskii", "Корней Чуковский"),
        ("agniya-barto", "Агния Барто"),
        ("fyodor-tyutchev", "Фёдор Тютчев"),
    ]
    for slug, name in EXTRA_AUTHORS:
        authors_map.setdefault(slug, name)

    authors_list = sorted(authors_map.items(), key=lambda x: x[1])
    progress_set(total_steps=base_steps + len(authors_list))

    bibliography = []
    seen_links = {it.get("link") for it in excerpts if it.get("link")}
    bib_lock = threading.Lock()

    # Параллельный обход авторов — главное узкое место.
    def crawl_one(slug_name):
        slug, name = slug_name
        # каждый поток должен иметь свою requests.Session чтобы не делить sock
        import requests
        s = requests.Session()
        return name, _crawl_author(slug, name, s) or []

    # Инкрементально сохраняем каждые N авторов — чтобы при обрыве
    # не потерять список URL всей библиотеки.
    authors_done = [0]
    SAVE_EVERY_N_AUTHORS = 10

    with ThreadPoolExecutor(max_workers=AUTHOR_WORKERS) as ex:
        futures = {ex.submit(crawl_one, an): an for an in authors_list}
        for fut in as_completed(futures):
            slug, name = futures[fut]
            step += 1
            try:
                _name, items = fut.result()
            except Exception:
                items = []
            with bib_lock:
                added = 0
                for it in items:
                    if it["link"] in seen_links:
                        continue
                    seen_links.add(it["link"])
                    bibliography.append(it)
                    added += 1
                authors_done[0] += 1
                # Чекпоинт: сохраняем снимок каждые N авторов
                if authors_done[0] % SAVE_EVERY_N_AUTHORS == 0:
                    save_data(excerpts + bibliography)
            progress_set(step=step, stage=f"Автор: {name} (+{added})",
                         items=len(excerpts) + len(bibliography))
            if CANCEL_EVENT.is_set():
                # Сохраним и прервёмся
                with bib_lock:
                    save_data(excerpts + bibliography)
                ex.shutdown(wait=False, cancel_futures=True)
                raise InterruptedError("cancelled")

    # Снимок после всех авторов
    save_data(excerpts + bibliography)
    all_items = excerpts + bibliography

    # Стадия 3: скачиваем полный текст каждого стиха — чтобы работало оффлайн
    # и без ожидания при клике «читать».
    poem_items = [it for it in all_items if it.get("type") in ("poem", "bibliography")
                  and it.get("link")]
    total_texts = len(poem_items)
    progress_set(total_steps=base_steps + len(authors_list) + total_texts)

    text_lock = threading.Lock()
    downloaded = [0]
    success = [0]
    fails = [0]

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_download_text_for_item, it): it for it in poem_items}
        for fut in as_completed(futures):
            it = futures[fut]
            ok = False
            try: ok = bool(fut.result())
            except Exception: pass
            with text_lock:
                downloaded[0] += 1
                if ok: success[0] += 1
                else: fails[0] += 1
                step_now = base_steps + len(authors_list) + downloaded[0]
                # Сохраняем каждые 30 — чтобы при сбое терять минимум
                if downloaded[0] % 30 == 0:
                    save_data(all_items)
            progress_set(step=step_now,
                         stage=f"Скачиваю тексты: {downloaded[0]}/{total_texts} · ✓{success[0]} ✗{fails[0]}",
                         items=len(all_items))
            if CANCEL_EVENT.is_set():
                with text_lock:
                    save_data(all_items)
                ex.shutdown(wait=False, cancel_futures=True)
                raise InterruptedError("cancelled")

    save_data(all_items)
    progress_set(step=base_steps + len(authors_list) + total_texts, stage="Готово",
                 items=len(all_items), ok=True, running=False)
    return all_items


def save_data(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- ИЗБРАННОЕ ----------
FAVORITES_FILE = DATA_DIR / "favorites.json"
_FAVORITES_LOCK = threading.Lock()


def load_favorites():
    """Возвращает list id-шников избранного. Безопасно если файла нет."""
    if not FAVORITES_FILE.exists():
        return []
    try:
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data]
            return []
    except Exception:
        return []


def save_favorites(ids):
    """Сохраняет избранное. ids — iterable строк."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = sorted({str(x) for x in (ids or []) if x})
    with _FAVORITES_LOCK:
        with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
    return cleaned


# ---------- КАДРЫ ИЗ КИНО ----------
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
SCREENSHOTS_FILE = DATA_DIR / "screenshots.json"
_SCREENSHOTS_LOCK = threading.Lock()


def load_screenshots():
    """Возвращает список метаданных кадров. Безопасно если файла нет."""
    if not SCREENSHOTS_FILE.exists():
        return []
    try:
        with open(SCREENSHOTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_screenshots(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SCREENSHOTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _resize_image_bytes(src_bytes, original_filename):
    """Уменьшает картинку до 1920 px по большей стороне и конвертит HEIC→JPEG.
    Правило размера — в CLAUDE.md. Если Pillow нет или что-то пошло не так,
    возвращает оригинальные байты, чтобы загрузка не ломалась.
    Возвращает (bytes, ext_без_точки).
    """
    ext = (original_filename.rsplit('.', 1)[-1] or 'jpg').lower()
    try:
        from PIL import Image
        import io
    except ImportError:
        return src_bytes, ext if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif') else 'jpg'

    if ext in ('heic', 'heif'):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            # без pillow-heif декодер HEIC не зарегистрирован — оставим как есть
            return src_bytes, ext

    try:
        im = Image.open(io.BytesIO(src_bytes))
        im.load()
    except Exception:
        return src_bytes, ext if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif') else 'jpg'

    big_side = max(im.size)
    heic = ext in ('heic', 'heif')
    needs_resize = big_side > 1920
    normal_fmt = ext in ('jpg', 'jpeg', 'png', 'webp')

    # Если формат нормальный и размер уже ≤1920 — не трогаем, сохраняем как есть.
    if not needs_resize and not heic and normal_fmt:
        return src_bytes, 'jpg' if ext == 'jpeg' else ext

    if needs_resize:
        im.thumbnail((1920, 1920), Image.LANCZOS)

    out = io.BytesIO()
    # PNG с прозрачностью оставляем PNG'ом, иначе сжимаем в JPEG.
    keep_png = (not heic) and ext == 'png' and im.mode in ('RGBA', 'LA', 'P')
    if keep_png:
        im.save(out, 'PNG', optimize=True)
        return out.getvalue(), 'png'
    if im.mode != 'RGB':
        im = im.convert('RGB')
    im.save(out, 'JPEG', quality=90, optimize=True, progressive=True)
    return out.getvalue(), 'jpg'


def add_screenshot(meta, file_bytes, original_filename):
    """Сохраняет картинку и метаданные. meta: dict с полями film/director/year/why/tags."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    # Нормализация: ресайз до 1920px и HEIC→JPEG (см. CLAUDE.md «Правило размера»).
    file_bytes, ext = _resize_image_bytes(file_bytes, original_filename)
    if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
        ext = 'jpg'
    base = re.sub(r'[^\w]+', '_',
                  f"{meta.get('film','noname')}_{meta.get('director','')}").strip('_').lower()[:60]
    if not base:
        base = 'screenshot'
    counter = 1
    while True:
        candidate = f"{base}_{counter:02d}.{ext}"
        if not (SCREENSHOTS_DIR / candidate).exists():
            break
        counter += 1
    target = SCREENSHOTS_DIR / candidate
    with open(target, 'wb') as f:
        f.write(file_bytes)

    with _SCREENSHOTS_LOCK:
        records = load_screenshots()
        rec = {
            "id": candidate.rsplit('.', 1)[0],
            "file": candidate,
            "film": meta.get('film', '').strip(),
            "director": meta.get('director', '').strip(),
            "year": meta.get('year') if isinstance(meta.get('year'), int)
                    else (int(meta['year']) if str(meta.get('year', '')).isdigit() else None),
            "why": (meta.get('why') or '').strip(),
            "tags": meta.get('tags') or [],
            "note": (meta.get('note') or '').strip(),
            "added": time.strftime('%Y-%m-%d'),
        }
        records.append(rec)
        save_screenshots(records)
    return rec


def update_screenshot(sid, meta):
    """Обновляет метаданные существующего кадра."""
    with _SCREENSHOTS_LOCK:
        records = load_screenshots()
        target = next((r for r in records if r.get('id') == sid), None)
        if not target:
            return None
        for k in ('film', 'director', 'year', 'why', 'tags', 'note'):
            if k in meta:
                v = meta[k]
                if k == 'year' and isinstance(v, str) and v.isdigit():
                    v = int(v)
                target[k] = v
        save_screenshots(records)
        return target


def delete_screenshot(sid):
    """Удаляет кадр (картинку + запись)."""
    with _SCREENSHOTS_LOCK:
        records = load_screenshots()
        target = next((r for r in records if r.get('id') == sid), None)
        if not target:
            return False
        path = SCREENSHOTS_DIR / target['file']
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        records = [r for r in records if r.get('id') != sid]
        save_screenshots(records)
        return True


def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data:
            item.setdefault("type", "poem" if "excerpt" in item else "bibliography")
            if "featured" not in item:
                item["featured"] = item.get("category") in FEATURED_CATEGORIES
            item.setdefault("category", "Каталог")
    else:
        data = []
    # Расширяем библиотеку стихами из локальных корпусов (HuggingFace, PDF, Wayback)
    # — это 195+ классиков, 18к стихов, которые иначе были бы невидимы.
    seen = set()
    for p in data:
        a = (p.get('author') or '').strip()
        t = _norm_text(p.get('title') or '')
        if a and t:
            seen.add((a, t))
    data.extend(_load_corpus_as_items(seen))
    # Подмешиваем цитаты из quotes_*.csv (Викицитатник и др.)
    data.extend(_load_quote_csvs())
    return data


# Кэш распарсенных корпусных записей. Парсим CSV один раз, потом каждый
# load_data() просто фильтрует по уже известным (author, title).
_CORPUS_ITEMS_CACHE = None
_CORPUS_ITEMS_LOCK = threading.Lock()


def _parse_corpus_items():
    """Разбирает все corpus_*.csv из DATA_DIR в плоский список записей
    в формате основной базы (poem-типа). Вызывается один раз, результат
    кэшируется."""
    import csv as _csv
    items = []
    paths = []
    for src in CORPUS_SOURCES:
        p = _corpus_path(src)
        if p.exists():
            paths.append((p, src["fields"], src.get("default_author", "")))
    for p in _discover_extra_csvs():
        paths.append((p, {'author': 'author', 'title': 'title', 'text': 'text'}, ''))
    for path, fields, default_author in paths:
        f_author = fields.get('author', 'author')
        f_title = fields.get('title', 'title')
        f_text = fields.get('text', 'text')
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    a = (row.get(f_author) or default_author or '').strip()
                    t = (row.get(f_title) or '').strip()
                    text = (row.get(f_text) or '').strip()
                    if not a or not t or not text:
                        continue
                    flat = ' '.join(text.split())
                    excerpt = flat[:220] + ('…' if len(flat) > 220 else '')
                    category = (row.get('themes/item/0') or '').strip() or 'Классика'
                    year = (row.get('date_from') or '').strip() or None
                    items.append({
                        "author": a,
                        "title": t,
                        "excerpt": excerpt,
                        "link": "",
                        "type": "poem",
                        "category": category,
                        "featured": False,
                        "text": text,
                        "year": year,
                    })
        except Exception as err:
            print(f"corpus parse error {path}: {err}", file=sys.stderr)
    return items


def _load_corpus_as_items(seen_keys):
    """Возвращает корпусные записи, отфильтрованные от дубликатов
    по (author, нормализованное название). Обновляет seen_keys на месте."""
    global _CORPUS_ITEMS_CACHE
    with _CORPUS_ITEMS_LOCK:
        if _CORPUS_ITEMS_CACHE is None:
            _CORPUS_ITEMS_CACHE = _parse_corpus_items()
    out = []
    for it in _CORPUS_ITEMS_CACHE:
        key = (it['author'], _norm_text(it['title']))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(it)
    return out


QUOTE_CATEGORIES = {
    # Музыканты
    'Виктор Цой': 'Цитаты музыкантов',
    'Курт Кобейн': 'Цитаты музыкантов',
    'Джон Леннон': 'Цитаты музыкантов',
    'Дэвид Боуи': 'Цитаты музыкантов',
    'Боб Дилан': 'Цитаты музыкантов',
    'Фредди Меркьюри': 'Цитаты музыкантов',
    'Борис Гребенщиков': 'Цитаты музыкантов',
    'Юрий Шевчук': 'Цитаты музыкантов',
    # Политики
    'Уинстон Черчилль': 'Цитаты политиков',
    'Авраам Линкольн': 'Цитаты политиков',
    'Джон Фицджеральд Кеннеди': 'Цитаты политиков',
    'Махатма Ганди': 'Цитаты политиков',
    # Режиссёры
    'Андрей Тарковский': 'Цитаты режиссёров',
    'Дэвид Линч': 'Цитаты режиссёров',
    'Стэнли Кубрик': 'Цитаты режиссёров',
    'Мартин Скорсезе': 'Цитаты режиссёров',
    # Философы
    'Фридрих Ницше': 'Цитаты философов',
    'Альбер Камю': 'Цитаты философов',
    'Бертран Рассел': 'Цитаты философов',
    'Славой Жижек': 'Цитаты философов',
    # Писатели
    'Сергей Довлатов': 'Цитаты писателей',
    'Курт Воннегут': 'Цитаты писателей',
    'Даниил Хармс': 'Цитаты писателей',
    'Иосиф Бродский': 'Цитаты писателей',
    'Венедикт Ерофеев': 'Цитаты писателей',
    'Чарльз Буковски': 'Цитаты писателей',
}


def _load_quote_csvs():
    """Читает все quotes_*.csv из DATA_DIR и возвращает их в формате записей
    основной базы: type='quote', excerpt=цитата, author/title/category.

    Категория определяется по автору через QUOTE_CATEGORIES, для незнакомых —
    общее «Цитаты». Это даёт фильтры на главной по типу личности.
    """
    if not DATA_DIR.exists():
        return []
    import csv as _csv
    out = []
    for path in sorted(DATA_DIR.glob('quotes_*.csv')):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    quote = (row.get('quote') or '').strip()
                    if len(quote) < 8:
                        continue
                    author = (row.get('author') or '').strip()
                    source = (row.get('source') or '').strip()
                    # Категория по автору — иначе общее «Цитаты»
                    category = QUOTE_CATEGORIES.get(author, 'Цитаты')
                    out.append({
                        "author": author,
                        "title": source,
                        "excerpt": quote,
                        "text": quote,
                        "link": "",
                        "type": "quote",
                        "category": category,
                        "featured": False,
                    })
        except Exception:
            continue
    return out


# Счётчик источников последнего батча. Сбрасывается в download_missing_texts.
SOURCE_STATS = {"corpus": 0, "wikisource": 0, "direct": 0, "wayback": 0, "fail": 0}
SOURCE_STATS_LOCK = threading.Lock()


# ---------- ЛОКАЛЬНЫЙ КОРПУС (HuggingFace) ----------
# Полностью автономный источник: один CSV-файл с ~16700 стихов классиков,
# скачивается единожды (~24 МБ), хранится рядом с poems.json. Доступен из любой
# страны — HuggingFace не блокируется. После загрузки работает без интернета.
_CORPUS_INDEX = None  # dict: ключ -> текст. None = ещё не загружен.
_CORPUS_LOCK = threading.Lock()
_CORPUS_META = {"loaded": False, "rows": 0, "keys": 0, "exists": False}


def _norm_text(s):
    """Нормализация для сравнения: lowercase, ё→е, убрать пунктуацию."""
    s = (s or '').lower()
    s = re.sub(r'[ёе]', 'е', s)
    s = re.sub(r'[«»""\'()…\.\,!?\-—–:;]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _norm_lastname(author):
    """Александр Пушкин → пушкин. Берём последнее слово."""
    parts = _norm_text(author).split()
    return parts[-1] if parts else ''


def _first_line_norm(text, limit=40):
    if not text:
        return ''
    line = text.split('\n')[0].strip()
    return _norm_text(line)[:limit]


def _corpus_path(src):
    return DATA_DIR / src["filename"]


def corpus_status():
    """Состояние всех корпусов для UI. Возвращает агрегированную статистику."""
    sources = []
    total_size = 0
    total_exists = 0
    for src in CORPUS_SOURCES:
        p = _corpus_path(src)
        exists = p.exists()
        size = p.stat().st_size if exists else 0
        total_size += size
        if exists:
            total_exists += 1
        sources.append({
            "id": src["id"],
            "title": src["title"],
            "exists": exists,
            "size_mb": round(size / 1024 / 1024, 2),
        })
    # Локальные дополнительные CSV — пользователь сам добавил
    for p in _discover_extra_csvs():
        size = p.stat().st_size
        total_size += size
        total_exists += 1
        sources.append({
            "id": p.stem,
            "title": p.stem.replace('corpus_', '').replace('_', ' ').title(),
            "exists": True,
            "size_mb": round(size / 1024 / 1024, 2),
            "local": True,
        })
    return {
        "exists": total_exists > 0,
        "all_loaded": total_exists >= len(CORPUS_SOURCES),
        "loaded": _CORPUS_META["loaded"],
        "rows": _CORPUS_META["rows"],
        "size_mb": round(total_size / 1024 / 1024, 2),
        "sources": sources,
    }


def _download_one_corpus(src, progress_cb=None):
    """Скачивает один CSV-корпус. Не пишет частичный файл."""
    import urllib.request
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = _corpus_path(src)
    tmp = target.with_suffix(target.suffix + '.partial')
    try:
        req = urllib.request.Request(src["url"],
                                     headers={'User-Agent': HEADERS['User-Agent']})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            chunk_size = 64 * 1024
            done = 0
            with open(tmp, 'wb') as f:
                while True:
                    if CANCEL_EVENT.is_set():
                        raise InterruptedError("cancelled")
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
        tmp.replace(target)
        return True
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def download_corpus(progress_cb=None):
    """Скачивает все недостающие CSV-корпуса из CORPUS_SOURCES.

    progress_cb(done_bytes, total_bytes) — для текущего файла. Состав файла
    меняется по ходу загрузки; вызов прогресса можно переоборачивать снаружи.
    """
    global _CORPUS_INDEX
    for src in CORPUS_SOURCES:
        if _corpus_path(src).exists():
            continue
        # Оборачиваем callback, чтобы он видел title текущего файла
        title = src["title"]
        wrapped = (lambda d, t, _title=title:
                   progress_cb(d, t, _title)) if progress_cb else None
        _download_one_corpus(src, wrapped)
    # Инвалидируем кэш индекса — перезагрузим при следующем обращении
    with _CORPUS_LOCK:
        _CORPUS_INDEX = None
    return True


def _discover_extra_csvs():
    """Возвращает список путей к локальным `corpus_*.csv` в DATA_DIR,
    которых нет в CORPUS_SOURCES. Это позволяет пользователю просто
    положить дополнительный CSV в папку с данными и оно подхватится."""
    if not DATA_DIR.exists():
        return []
    known = {_corpus_path(s).name for s in CORPUS_SOURCES}
    extra = []
    for p in DATA_DIR.glob('corpus_*.csv'):
        if p.name not in known and p.name != 'corpus.csv':
            extra.append(p)
    return sorted(extra)


def _load_corpus_into_index():
    """Читает все CSV из CORPUS_SOURCES + автообнаруженные corpus_*.csv
    из DATA_DIR, строит общий индекс. Идемпотентно."""
    global _CORPUS_INDEX
    import csv

    def add_csv(path, fields, default_author=''):
        nonlocal rows
        f_author = fields.get('author', 'author')
        f_title = fields.get('title', 'title')
        f_text = fields.get('text', 'text')
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows += 1
                raw_author = row.get(f_author, '') or default_author
                author = _norm_lastname(raw_author)
                text = (row.get(f_text) or '').strip()
                name = _norm_text(row.get(f_title, ''))
                if not author or not text:
                    continue
                if name:
                    index.setdefault((author, name), text)
                    if len(name) > 15:
                        index.setdefault((author, name[:30]), text)
                fl = _first_line_norm(text)
                if fl:
                    index.setdefault((author, 'FL', fl), text)
                    if len(fl) > 15:
                        index.setdefault((author, 'FL', fl[:25]), text)

    index = {}
    rows = 0
    any_exists = False
    # Известные источники
    for src in CORPUS_SOURCES:
        path = _corpus_path(src)
        if not path.exists():
            continue
        any_exists = True
        add_csv(path, src["fields"], src.get("default_author", ""))
    # Локальные дополнительные (например, спарсенные из PDF) — формат author,title,text
    for path in _discover_extra_csvs():
        any_exists = True
        add_csv(path, {'author': 'author', 'title': 'title', 'text': 'text'})

    _CORPUS_INDEX = index
    _CORPUS_META.update(loaded=any_exists, rows=rows, keys=len(index),
                         exists=any_exists)


def ensure_corpus_loaded():
    """Загружает корпус в память, если ещё не загружен. Безопасно для потоков."""
    with _CORPUS_LOCK:
        if _CORPUS_INDEX is None:
            _load_corpus_into_index()


def _try_corpus(item):
    """Ищет стих в локальном корпусе. Возвращает текст или None."""
    if _CORPUS_INDEX is None:
        ensure_corpus_loaded()
    if not _CORPUS_INDEX:
        return None
    a = _norm_lastname(item.get('author', ''))
    t = _norm_text(item.get('title', ''))
    if not a or not t:
        return None
    # 1) Точное название
    text = _CORPUS_INDEX.get((a, t))
    if text:
        return text
    # 2) Префикс названия
    if len(t) > 15:
        text = _CORPUS_INDEX.get((a, t[:30]))
        if text:
            return text
    # 3) Название как первая строка стиха
    text = _CORPUS_INDEX.get((a, 'FL', t))
    if text:
        return text
    if len(t) > 15:
        text = _CORPUS_INDEX.get((a, 'FL', t[:25]))
        if text:
            return text
    return None



def _extract_poem_text(html_text):
    """Парсит HTML страницы askbooka и возвращает чистый текст или None."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, 'lxml')
    body_el = (soup.select_one('.field-name-field-stih .field-item')
               or soup.select_one('.field-name-body .field-item')
               or soup.select_one('article .content'))
    if body_el:
        text = clean_text(body_el.get_text("\n"))
        if text and len(text) > 10:
            return text[:20000]
    return None


def _try_direct(url, session):
    """Прямой запрос на askbooka.ru. Короткий таймаут — если сайт лежит,
    сразу падаем в fallback. Возвращает текст или None."""
    try:
        r = session.get(url, headers=HEADERS, timeout=7)
        if r.status_code == 200:
            r.encoding = 'utf-8'
            return _extract_poem_text(r.text)
    except Exception:
        pass
    return None


def _try_wikisource(item, session):
    """Ищет стих в ru.wikisource.org через MediaWiki API.

    Стратегия: поиск по `intitle:"название" фамилия`, затем — если в результате
    нашлась страница с фамилией автора в заголовке — берём её plaintext через
    action=query+prop=extracts.

    Покрывает классиков, у которых уже истекли авторские права (Пушкин, Блок,
    Ахматова, Маяковский, Апухтин и т.д.). Современных и недавних — нет.
    Wikisource доступен из любой страны без VPN.
    """
    import urllib.parse
    author = item.get('author') or ''
    title = item.get('title') or ''
    if not author or not title:
        return None
    title_clean = re.sub(r'[«»""]+', '', title).strip().rstrip('.…')
    title_short = title_clean[:30]
    lastname = _norm_lastname(author)
    if not lastname:
        return None
    lastname_norm = re.sub(r'[^а-я]', '', _norm_text(author).split()[-1]
                           if _norm_text(author).split() else '')

    search_queries = [
        f'intitle:"{title_short}" {lastname}',
        f'"{title_short}" {lastname}',
    ]
    page_title = None
    for q in search_queries:
        params = {
            'action': 'query', 'list': 'search', 'srsearch': q,
            'srlimit': 5, 'format': 'json', 'srnamespace': 0,
        }
        url = 'https://ru.wikisource.org/w/api.php?' + urllib.parse.urlencode(params)
        try:
            r = session.get(url, headers=WIKIMEDIA_HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            d = r.json()
            for hit in d.get('query', {}).get('search', []):
                cand = hit['title']
                cand_norm = re.sub(r'[^а-я]', '', _norm_text(cand))
                # Принимаем только если фамилия автора в названии страницы.
                # Это отсекает случайные совпадения по тексту (типа Высоцкий → Жданов).
                if lastname_norm and lastname_norm in cand_norm:
                    page_title = cand
                    break
        except Exception:
            continue
        if page_title:
            break
    if not page_title:
        return None

    # Извлекаем plaintext страницы
    extract_url = 'https://ru.wikisource.org/w/api.php?' + urllib.parse.urlencode({
        'action': 'query', 'prop': 'extracts', 'explaintext': '1',
        'titles': page_title, 'format': 'json', 'redirects': '1',
    })
    try:
        r = session.get(extract_url, headers=WIKIMEDIA_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()
        pages = d.get('query', {}).get('pages', {})
        for _pid, pdata in pages.items():
            extract = pdata.get('extract') or ''
            text = _clean_wikisource_extract(extract)
            if text and len(text) > 20:
                return text[:20000]
    except Exception:
        return None
    return None


def _clean_wikisource_extract(extract):
    """Очищает plain-text из Викитеки от мусора: заголовков (== ... ==),
    шаблонов «Редакции», «Примечания», «Источник», предупреждений о
    дореформенной орфографии и т.п. Оставляет тело стиха."""
    if not extract:
        return ''
    extract = clean_text(extract)
    lines = extract.split('\n')
    out = []
    skip = False
    for line in lines:
        low = line.lower().strip()
        if not low:
            if out:
                out.append('')
            continue
        # Маркеры конца полезного контента
        if low.startswith(('источник', 'см. также', 'примечани',
                           'категория', 'это произведение',
                           'литература',
                           '== примечани', '== источник', '== ссылк',
                           '== см. также', '== литература')):
            break
        # Заголовок секции — пропускаем
        if line.strip().startswith('==') and line.strip().endswith('=='):
            # «== Редакции ==» — обычно дальше идут варианты, прекращаем
            head = line.strip().strip('=').strip().lower()
            if head in ('редакции', 'варианты', 'правки', 'примечания'):
                break
            skip = False
            continue
        # «дореформенная орфография» — мета, пропускаем строку
        if 'дореформенная орфография' in low or 'современная орфография' in low:
            continue
        out.append(line)
    text = '\n'.join(out).strip()
    # Убираем повторяющиеся пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def _try_wayback(url, session):
    """Запасной канал — Wayback Machine (web.archive.org). Архив отдаёт
    последний снимок страницы, если есть. Доступен из любой страны
    без VPN — это .org домен, который никто не блокирует.

    Возвращает текст или None.
    """
    # /web/0/<url> = «последний доступный снапшот». Архив сам выберет.
    archive_url = f"https://web.archive.org/web/0/{url}"
    try:
        r = session.get(archive_url, headers=HEADERS, timeout=15,
                        allow_redirects=True)
        if r.status_code == 200:
            r.encoding = 'utf-8'
            return _extract_poem_text(r.text)
    except Exception:
        pass
    return None


def _download_text_for_item(item):
    """Скачивает текст стиха в item['text']. Безопасно, без исключений.

    Стратегия (по убыванию приоритета — от мгновенного к медленному):
    1) Локальный корпус (CSV-файлы с HuggingFace, ~17к стихов, оффлайн).
    2) Wikisource API — для классиков, не попавших в корпус (PD-произведения).
    3) Прямой askbooka.ru (быстрый таймаут 7с).
    4) Wayback Machine — архив сайта.

    Уважает CANCEL_EVENT — мгновенно возвращает False, если жмут «Стоп».
    Помечает источник в SOURCE_STATS — для показа в прогрессе.
    """
    if CANCEL_EVENT.is_set():
        return False

    # 1) Локальный корпус — мгновенно, без сети
    text = _try_corpus(item)
    if text:
        item["text"] = text
        with SOURCE_STATS_LOCK:
            SOURCE_STATS["corpus"] += 1
        return True

    if CANCEL_EVENT.is_set():
        return False

    import requests
    s = requests.Session()
    url = item["link"]

    # 2) Wikisource — для классиков, которых нет в локальном корпусе
    text = _try_wikisource(item, s)
    if text:
        item["text"] = text
        with SOURCE_STATS_LOCK:
            SOURCE_STATS["wikisource"] += 1
        return True

    if CANCEL_EVENT.is_set():
        return False

    # 3) Прямой askbooka
    text = _try_direct(url, s)
    if text:
        item["text"] = text
        with SOURCE_STATS_LOCK:
            SOURCE_STATS["direct"] += 1
        return True

    if CANCEL_EVENT.is_set():
        return False

    # 4) Архив
    text = _try_wayback(url, s)
    if text:
        item["text"] = text
        with SOURCE_STATS_LOCK:
            SOURCE_STATS["wayback"] += 1
        return True

    with SOURCE_STATS_LOCK:
        SOURCE_STATS["fail"] += 1
    return False


def _check_connectivity():
    """Возвращает True, если хоть какой-то источник доступен:
    локальный корпус (вообще не требует сети), askbooka.ru или Wayback.
    """
    # 0) Локальный корпус — самый быстрый и не требует интернета вовсе.
    if CORPUS_FILE.exists():
        ensure_corpus_loaded()
        if _CORPUS_INDEX:
            return True

    import requests
    # 1) Прямой askbooka — короткий таймаут
    try:
        r = requests.get(f"{BASE_URL}/", headers=HEADERS, timeout=6)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    # 2) Wayback Machine — fallback. Если архив открывается, мы всё равно
    # сможем большую часть стихов вытащить.
    try:
        r = requests.get("https://archive.org/", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    return False


def download_missing_texts():
    """Догружает полный текст только для стихов без поля text.

    Ключевые свойства:
    - Инкрементальное сохранение: после каждого успешно скачанного стиха
      пишем в poems.json (но не чаще 1 раза в 2 сек — чтобы не мучать диск).
    - Возобновление: при повторном запуске уже скачанные стихи пропускаются
      (фильтр `not it.get("text")` в начале).
    - Отмена: если нажата «Остановить» (CANCEL_EVENT), выходим аккуратно
      и сохраняем то, что успели.
    - Stall detection: если подряд 50 запросов провалились без единого
      успеха — сайт упал, сами останавливаемся.
    """
    CANCEL_EVENT.clear()
    with SOURCE_STATS_LOCK:
        SOURCE_STATS["corpus"] = 0
        SOURCE_STATS["wikisource"] = 0
        SOURCE_STATS["direct"] = 0
        SOURCE_STATS["wayback"] = 0
        SOURCE_STATS["fail"] = 0
    # Корпус — основной источник. Если каких-то CSV ещё нет — скачаем.
    # Покрывает большую часть классики мгновенно, без обращения к askbooka.
    needs_download = [s for s in CORPUS_SOURCES if not _corpus_path(s).exists()]
    if needs_download:
        progress_set(running=True, total_steps=1, step=0,
                     stage="Скачиваю локальные корпуса (разово)...",
                     items=0, ok=None, error=None)
        try:
            def _cb(done, total, title):
                pct = (done * 100 // total) if total else 0
                mb_done = done / 1024 / 1024
                mb_tot = total / 1024 / 1024 if total else 0
                progress_set(stage=f"Корпус «{title}»: {mb_done:.1f}/{mb_tot:.1f} МБ ({pct}%)")
            download_corpus(progress_cb=_cb)
        except InterruptedError:
            progress_set(running=False, ok=True,
                         stage="Загрузка корпуса отменена. Можно повторить позже.")
            return
        except Exception as e:
            progress_set(running=False, ok=False,
                         error=f"Не удалось скачать корпус: {e}\n\n"
                               f"Можно повторить позже — кнопка «📥 Скачать тексты» "
                               f"запустит загрузку снова.",
                         stage="Ошибка загрузки корпуса")
            return
    ensure_corpus_loaded()

    data = load_data()
    missing = [it for it in data if it.get("link") and not it.get("text")
               and it.get("type") in ("poem", "bibliography")]
    total = len(missing)
    already = len(data) - total
    progress_set(running=True, total_steps=total, step=0,
                 stage="Проверяю соединение...",
                 items=already, ok=None, error=None)
    if total == 0:
        progress_set(running=False, ok=True, stage="Все тексты уже скачаны",
                     step=0, total_steps=0, items=len(data))
        return

    # Тест связи перед массовой загрузкой — пускаем дальше, если работает
    # хоть один источник: askbooka ИЛИ web.archive.org.
    if not _check_connectivity():
        progress_set(running=False, ok=False,
                     error="Не отвечают ни askbooka.ru, ни web.archive.org.\n\n"
                           "Похоже, у тебя сейчас вообще нет интернета. "
                           "Проверь подключение и попробуй снова. "
                           "База не изменена.",
                     stage="Нет интернета")
        return

    lock = threading.Lock()
    done = [0]
    success = [0]
    failed = [0]
    fail_streak = [0]        # подряд провалов без успеха — для stall detection
    last_save_ts = [time.time()]
    SAVE_EVERY_SEC = 2.0     # не чаще раза в 2 сек
    SAVE_EVERY_N = 10        # или каждые 10 успешных
    STALL_LIMIT = 100        # подряд провалов → оба источника мертвы

    def _maybe_save(force=False):
        """Сохраняем под локом с троттлингом. Вызывать ТОЛЬКО под lock."""
        now = time.time()
        if force or (now - last_save_ts[0] >= SAVE_EVERY_SEC) or \
                (success[0] > 0 and success[0] % SAVE_EVERY_N == 0):
            save_data(data)
            last_save_ts[0] = now

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_download_text_for_item, it): it for it in missing}
        for fut in as_completed(futures):
            ok_flag = False
            try: ok_flag = bool(fut.result())
            except Exception: pass
            stop_now = False
            with lock:
                done[0] += 1
                if ok_flag:
                    success[0] += 1
                    fail_streak[0] = 0
                else:
                    failed[0] += 1
                    fail_streak[0] += 1
                # Сохраняем инкрементально (троттлинг + каждые N успешных)
                if ok_flag:
                    _maybe_save()
                # Stall: 50 fail подряд без единого успеха → сайт мёртв
                if fail_streak[0] >= STALL_LIMIT and success[0] == 0:
                    CANCEL_EVENT.set()
                    stop_now = True
                # Stall-2: 50 подряд fail даже если раньше были успехи
                # (сайт отвалился в середине)
                if fail_streak[0] >= STALL_LIMIT and success[0] > 0:
                    CANCEL_EVENT.set()
                    stop_now = True
            in_db = already + success[0]
            with SOURCE_STATS_LOCK:
                src_c = SOURCE_STATS["corpus"]
                src_ws = SOURCE_STATS["wikisource"]
                src_d = SOURCE_STATS["direct"]
                src_w = SOURCE_STATS["wayback"]
            progress_set(step=done[0],
                         stage=f"Скачиваю: {done[0]}/{total} · ✓{success[0]} ✗{failed[0]} "
                               f"· источник: корпус {src_c} / викитека {src_ws} / askbooka {src_d} / архив {src_w} · в базе {in_db}",
                         items=in_db)
            if CANCEL_EVENT.is_set():
                break  # прекращаем обрабатывать новые futures

        # Если вышли по break — оставшиеся futures всё ещё висят.
        # cancel_futures=True не даёт им стартовать (Python 3.9+),
        # но те что уже в работе — дождёмся с коротким таймаутом.
        if CANCEL_EVENT.is_set():
            ex.shutdown(wait=False, cancel_futures=True)

    # Финальный save — обязательно
    with lock:
        save_data(data)

    in_db_final = already + success[0]
    with SOURCE_STATS_LOCK:
        src_c = SOURCE_STATS["corpus"]
        src_ws = SOURCE_STATS["wikisource"]
        src_d = SOURCE_STATS["direct"]
        src_w = SOURCE_STATS["wayback"]
    src_summary = (f"корпус: {src_c}, викитека: {src_ws}, "
                   f"askbooka: {src_d}, архив: {src_w}")
    if CANCEL_EVENT.is_set() and fail_streak[0] >= STALL_LIMIT:
        # Остановились сами: оба источника отвалились подряд STALL_LIMIT раз
        progress_set(running=False, ok=False,
                     error=f"И askbooka.ru, и архив (web.archive.org) перестали "
                           f"отвечать после {done[0]} запросов.\n\n"
                           f"В этой сессии скачано: {success[0]} ({src_summary}). "
                           f"Всего в базе: {in_db_final} из {len(data)}.\n\n"
                           f"Всё скачанное сохранено. Нажми «📥 Скачать тексты» "
                           f"ещё раз через 5-10 минут — докачаем с этого места.",
                     stage=f"Источники не отвечают",
                     items=in_db_final)
    elif CANCEL_EVENT.is_set():
        # Пользователь нажал «Остановить»
        progress_set(running=False, ok=True,
                     stage=f"Остановлено. Скачано {success[0]} ({src_summary}). "
                           f"В базе: {in_db_final}/{len(data)}. Нажми ещё раз — продолжим.",
                     items=in_db_final)
    elif success[0] == 0:
        progress_set(running=False, ok=False,
                     error=f"Не удалось скачать ни одного стиха из {total}. "
                           f"И askbooka, и архив сейчас недоступны. "
                           f"Попробуй через 5-10 минут (или включи VPN).",
                     stage="Источники не ответили",
                     items=in_db_final)
    else:
        progress_set(running=False, ok=True,
                     stage=f"Готово! Скачано {success[0]} из {total} "
                           f"({src_summary})"
                           + (f", не удалось {failed[0]}" if failed[0] else "")
                           + f". В базе: {in_db_final}",
                     items=in_db_final)


# ---------- ПОЛНЫЙ ТЕКСТ СТИХА ----------
_POEM_CACHE = {}
_POEM_CACHE_LOCK = threading.Lock()


def fetch_poem_text(url):
    """Подгружает страницу стиха и возвращает текст.

    Стратегия: сначала локальный корпус (мгновенно, без сети), потом прямой
    askbooka, потом Wayback. Так стих откроется даже когда оба сайта лежат —
    если он есть в корпусе.
    """
    if not url:
        raise ValueError("URL пустой")
    parsed = urlparse(url)
    if parsed.netloc != BASE_HOST:
        raise ValueError("Разрешены только URL askbooka.ru")

    with _POEM_CACHE_LOCK:
        cached = _POEM_CACHE.get(url)
        if cached is not None:
            return cached

    # 0) Локальный корпус — найдём по author/title из локальной базы
    try:
        all_data = load_data()
        meta = next((it for it in all_data if it.get("link") == url), None)
        if meta:
            corpus_text = _try_corpus(meta)
            if corpus_text:
                result = {
                    "title": meta.get("title", "") or "",
                    "text": corpus_text,
                    "link": url,
                    "source": "локальный корпус",
                }
                with _POEM_CACHE_LOCK:
                    _POEM_CACHE[url] = result
                return result
    except Exception:
        pass

    import requests
    from bs4 import BeautifulSoup
    s = requests.Session()
    html = None
    source = None
    last_err = None

    # 1) Прямой askbooka
    try:
        r = s.get(url, headers=HEADERS, timeout=POEM_FETCH_TIMEOUT)
        if r.status_code == 200:
            r.encoding = 'utf-8'
            html = r.text
            source = "askbooka"
    except Exception as e:
        last_err = e

    # 2) Архив, если прямой не дал
    if not html:
        try:
            r = s.get(f"https://web.archive.org/web/0/{url}", headers=HEADERS,
                      timeout=15, allow_redirects=True)
            if r.status_code == 200:
                r.encoding = 'utf-8'
                html = r.text
                source = "архив"
        except Exception as e:
            last_err = e

    if not html:
        raise RuntimeError(f"Не отвечают ни askbooka, ни архив. ({last_err})")

    soup = BeautifulSoup(html, 'lxml')

    title = ""
    h1 = soup.select_one('h1.title, h1#page-title, h1')
    if h1:
        title = h1.get_text(strip=True)

    body_el = (soup.select_one('.field-name-field-stih .field-item')
               or soup.select_one('.field-name-body .field-item')
               or soup.select_one('article .content'))
    text = clean_text(body_el.get_text("\n")) if body_el else ""

    if not text:
        # фолбэк: весь основной контент
        main = soup.select_one('#main-content, main, article') or soup
        text = clean_text(main.get_text("\n"))

    result = {"title": title, "text": text, "link": url, "source": source}
    with _POEM_CACHE_LOCK:
        _POEM_CACHE[url] = result
    return result


# ---------- HTML ИНТЕРФЕЙС ----------
HTML_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Сеанс</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=Space+Grotesk:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
:root {
  --font-display: 'DM Serif Display', 'Playfair Display', Georgia, serif;
  --font-body:    'EB Garamond', 'Cormorant Garamond', Georgia, serif;
  --font-ui:      'Space Grotesk', 'Inter', system-ui, sans-serif;
  --font-mono:    'JetBrains Mono', ui-monospace, monospace;

  --ink:        #EDE4D3;
  --ink-2:      #B8AB93;
  --ink-3:      #6B5F4F;
  --paper:      #1A1714;
  --paper-2:    #221D19;
  --paper-3:    #2C2621;
  --accent:     #B88A60;
  --accent-2:   #D4A574;
  --alert:      #A03A2E;
  --line:       #3A332D;
  --line-2:     #2A2520;
  --stamp:      #8A5A44;

  --max:   1320px;
  --pad:   clamp(20px, 4vw, 48px);
  --radius: 2px;

  color-scheme: dark;
}

body[data-theme='light'] {
  --ink:     #2B2520;
  --ink-2:   #6B5F4F;
  --ink-3:   #B8AB93;
  --paper:   #F2ECE0;
  --paper-2: #EBE4D4;
  --paper-3: #FFFFFF;
  --accent:  #8B5A3C;
  --accent-2:#B88A60;
  --alert:   #A03A2E;
  --line:    #D9CFB8;
  --line-2:  #E6DEC9;
  --stamp:   #8B5A3C;
  color-scheme: light;
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  min-height: 100vh;
  overflow-x: hidden;
}

body::before {
  content: '';
  position: fixed; inset: 0;
  pointer-events: none;
  z-index: 9999;
  opacity: .08;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' seed='4'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .7 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
}
body[data-theme='light']::before { opacity: .12; mix-blend-mode: multiply; }

button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; padding: 0; }
input, textarea { font: inherit; color: inherit; background: none; border: 0; }
a { color: inherit; text-decoration: none; }
img { max-width: 100%; display: block; }

.shell {
  max-width: var(--max);
  margin: 0 auto;
  padding: 0 var(--pad);
  position: relative;
}

.masthead {
  padding: 18px 0 14px;
  border-bottom: 1px solid var(--line);
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 24px;
}
.masthead .issue {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .22em;
  text-transform: uppercase;
  color: var(--ink-2);
  display: flex;
  gap: 20px;
  align-items: center;
}
.masthead .issue .dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--accent);
  display: inline-block;
}
.masthead .brand {
  text-align: center;
  font-family: var(--font-display);
  font-size: 26px;
  letter-spacing: .02em;
  position: relative;
}
.masthead .brand::before,
.masthead .brand::after {
  content: '';
  position: absolute;
  top: 50%;
  width: 40px;
  height: 1px;
  background: var(--line);
}
.masthead .brand::before { right: calc(100% + 18px); }
.masthead .brand::after  { left:  calc(100% + 18px); }
.masthead .theme-switch {
  justify-self: end;
  display: flex;
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 999px;
}
.masthead .theme-switch button {
  width: 30px; height: 30px;
  display: grid; place-items: center;
  border-radius: 50%;
  color: var(--ink-2);
  transition: all .2s;
  font-size: 13px;
}
.masthead .theme-switch button.active {
  background: var(--accent);
  color: var(--paper);
}

.tagline {
  text-align: center;
  padding: 18px 18px 16px;
  max-width: 640px;
  margin: 0 auto;
}
.tagline .lead {
  font-family: var(--font-display);
  font-size: 20px;
  line-height: 1.35;
  letter-spacing: .01em;
  color: var(--ink);
  margin: 0 0 6px;
}
.tagline .lead em {
  color: var(--accent);
  font-style: italic;
}
.tagline .sub {
  font-family: var(--font-body);
  font-size: 14px;
  font-style: italic;
  color: var(--ink-2);
  margin: 0;
  line-height: 1.5;
}
@media (max-width: 600px) {
  .tagline { padding: 14px 14px 12px; }
  .tagline .lead { font-size: 17px; }
  .tagline .sub { font-size: 13px; }
}

.sections {
  display: flex;
  justify-content: center;
  gap: 0;
  padding: 14px 0 0;
  border-bottom: 1px solid var(--line);
}
.sections .tab {
  font-family: var(--font-display);
  font-size: 18px;
  padding: 6px 36px 14px;
  color: var(--ink-3);
  position: relative;
  letter-spacing: .01em;
  transition: color .2s;
}
.sections .tab:hover { color: var(--ink-2); }
.sections .tab.active { color: var(--ink); }
.sections .tab.active::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: -1px;
  transform: translateX(-50%);
  width: 60%;
  height: 2px;
  background: var(--accent);
}
.sections .tab .num {
  font-family: var(--font-ui);
  font-size: 9px;
  letter-spacing: .2em;
  color: var(--ink-3);
  display: block;
  margin-bottom: 2px;
}
.sections .tab.active .num { color: var(--accent); }

.subnav {
  display: flex;
  justify-content: center;
  gap: 32px;
  padding: 10px 0 6px;
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--ink-2);
}
.subnav button { position: relative; padding: 4px 0; color: var(--ink-2); }
.subnav button.active { color: var(--ink); }
.subnav button.active::after {
  content: '·'; position: absolute; left: 50%; top: 100%;
  color: var(--accent);
  transform: translateX(-50%);
  font-size: 16px;
}

.hero {
  padding: 28px 0 18px;
  text-align: center;
  position: relative;
}
.hero.hero-lg { padding: 44px 0 28px; }
.hero .kicker {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .3em;
  text-transform: uppercase;
  color: var(--accent);
  display: inline-flex;
  align-items: center;
  gap: 12px;
}
.hero .kicker::before, .hero .kicker::after {
  content: ''; width: 24px; height: 1px; background: var(--accent); opacity: .5;
}
.hero h1 {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: clamp(32px, 5vw, 56px);
  line-height: .98;
  margin: 8px 0 6px;
  letter-spacing: -.02em;
}
.hero.hero-lg h1 { font-size: clamp(48px, 8vw, 96px); margin: 14px 0 12px; }
.hero h1 em {
  font-family: var(--font-body);
  font-style: italic;
  font-weight: 400;
  color: var(--accent);
}
.hero .lede {
  font-family: var(--font-body);
  font-style: italic;
  font-size: 15px;
  color: var(--ink-2);
  max-width: 480px;
  margin: 0 auto;
}
.hero.hero-lg .lede { font-size: 17px; }

.toolbar {
  display: grid;
  gap: 10px;
  margin: 14px auto 18px;
  max-width: 860px;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border: 1px solid var(--line);
  border-radius: 2px;
  background: var(--paper-2);
  transition: border-color .2s;
}
.search-box:focus-within { border-color: var(--accent); }
.search-box svg { width: 16px; height: 16px; color: var(--ink-3); flex: 0 0 auto; }
.search-box input {
  flex: 1;
  outline: 0;
  font-family: var(--font-body);
  font-size: 17px;
  color: var(--ink);
}
.search-box input::placeholder { color: var(--ink-3); font-style: italic; }
.search-box .shortcut {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  border: 1px solid var(--line);
  padding: 2px 6px;
  border-radius: 3px;
}

.filter-drawer {
  border: 1px solid var(--line);
  background: var(--paper-2);
  border-radius: 2px;
}
.filter-drawer > summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .22em;
  text-transform: uppercase;
  color: var(--ink-2);
  transition: color .15s;
}
.filter-drawer > summary:hover { color: var(--accent); }
.filter-drawer > summary::-webkit-details-marker { display: none; }
.filter-drawer > summary::marker { content: ''; }
.filter-drawer > summary .summary-left {
  display: flex; align-items: center; gap: 14px; min-width: 0; flex: 1;
}
.filter-drawer > summary .active-chip {
  color: var(--accent);
  font-family: var(--font-body);
  font-style: italic;
  font-size: 14px;
  letter-spacing: 0;
  text-transform: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.filter-drawer > summary .caret { color: var(--ink-3); transition: transform .2s; font-size: 10px; }
.filter-drawer[open] > summary .caret { transform: rotate(180deg); }
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: center;
  padding: 12px 14px 16px;
  border-top: 1px solid var(--line-2);
  max-height: 260px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--line) transparent;
}
.filters::-webkit-scrollbar { width: 8px; }
.filters::-webkit-scrollbar-thumb { background: var(--line); border-radius: 4px; }
.filters .chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-family: var(--font-ui);
  font-size: 12px;
  color: var(--ink-2);
  transition: all .15s;
  letter-spacing: .01em;
}
.filters .chip .c {
  font-size: 10px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
.filters .chip:hover { border-color: var(--accent); color: var(--ink); }
.filters .chip.active {
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
}
.filters .chip.active .c { color: var(--paper); opacity: .6; }

.actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 6px;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 22px;
  border: 1px solid var(--line);
  border-radius: 2px;
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .22em;
  text-transform: uppercase;
  transition: all .15s;
  color: var(--ink);
}
.btn:hover { border-color: var(--accent); color: var(--accent); }
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--paper);
}
.btn.primary:hover { background: var(--accent-2); border-color: var(--accent-2); color: var(--paper); }
.btn.ghost { background: transparent; border-color: var(--line); color: var(--ink-2); }
.btn.ghost:hover { border-color: var(--accent); color: var(--accent); }
.btn .glyph { font-size: 14px; }
.btn svg { width: 14px; height: 14px; flex: none; }

.overflow-menu { position: relative; display: inline-block; }
.overflow-menu > summary {
  list-style: none;
  cursor: pointer;
}
.overflow-menu > summary::-webkit-details-marker { display: none; }
.overflow-menu > summary::marker { content: ''; }
.overflow-menu[open] > summary { border-color: var(--accent); color: var(--accent); }
.overflow-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 280px;
  background: var(--paper-3);
  border: 1px solid var(--line);
  box-shadow: 0 20px 40px -12px rgba(0,0,0,.6), 0 0 0 1px var(--line-2);
  padding: 10px 0;
  z-index: 50;
  animation: overflowIn .14s ease-out forwards;
}
@keyframes overflowIn {
  from { transform: translateY(-4px); }
  to   { transform: translateY(0); }
}
.overflow-head, .overflow-foot {
  padding: 8px 18px;
  font-family: var(--font-ui);
  font-size: 9.5px;
  letter-spacing: .28em;
  text-transform: uppercase;
  color: var(--ink-3);
}
.overflow-foot { border-top: 1px solid var(--line-2); margin-top: 6px; padding-top: 10px; font-style: italic; letter-spacing: .2em; }
.overflow-item {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  padding: 10px 18px;
  background: transparent;
  border: 0;
  text-align: left;
  cursor: pointer;
  color: var(--ink);
  font-family: var(--font-ui);
  transition: background .12s;
}
.overflow-item:hover { background: var(--paper-2); color: var(--accent); }
.overflow-item svg { width: 18px; height: 18px; flex: none; color: var(--ink-2); }
.overflow-item:hover svg { color: var(--accent); }
.overflow-item .l { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.overflow-item .t { font-size: 13px; letter-spacing: .02em; }
.overflow-item .h { font-size: 10.5px; color: var(--ink-3); letter-spacing: .12em; text-transform: uppercase; }

.card-stack {
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 14px 0 8px;
}
.card-stack > * { animation: cardIn .35s ease both; }
.card-stack > *:nth-child(1) { animation-delay: 0s; }
.card-stack > *:nth-child(2) { animation-delay: .05s; }
.card-stack > *:nth-child(3) { animation-delay: .10s; }
.card-stack > *:nth-child(4) { animation-delay: .15s; }
.card-stack > *:nth-child(5) { animation-delay: .20s; }
@keyframes cardIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.status-bar {
  text-align: center;
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--ink-3);
  margin-top: 8px;
}
.status-bar span { color: var(--ink-2); }

.poem-card {
  background: var(--paper-2);
  border: 1px solid var(--line);
  padding: 0;
  margin: 14px auto;
  max-width: 760px;
  position: relative;
  transition: transform .3s ease, border-color .3s;
}
body[data-theme='light'] .poem-card { background: #FBF6EA; }
.poem-card:hover { border-color: var(--accent); }
.poem-card .colophon {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 28px;
  border-bottom: 1px solid var(--line-2);
  font-family: var(--font-ui);
  font-size: 9.5px;
  letter-spacing: .22em;
  text-transform: uppercase;
  color: var(--ink-3);
}
.poem-card .colophon .folio {
  font-variant-numeric: tabular-nums;
  color: var(--ink-2);
}
.poem-card .colophon .cat { color: var(--accent); }
.poem-card .excerpt {
  padding: 26px 32px 20px;
  font-family: var(--font-body);
  font-size: 22px;
  line-height: 1.5;
  color: var(--ink);
}
.poem-card .excerpt p { margin: 0; text-indent: 0; }
.poem-card .excerpt p + p { margin-top: 0; }
.poem-card .excerpt .drop {
  font-family: var(--font-display);
  font-size: 38px;
  line-height: 1;
  color: var(--accent);
  margin-right: 1px;
  font-weight: 400;
}
.poem-card .meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 14px 28px 16px;
  border-top: 1px solid var(--line-2);
}
.poem-card .meta .who {
  font-family: var(--font-body);
  font-style: italic;
  font-size: 15px;
}
.poem-card .meta .who .author {
  color: var(--ink);
  border-bottom: 1px dashed var(--ink-3);
  cursor: pointer;
  transition: color .2s;
  padding-bottom: 1px;
}
.poem-card .meta .who .author:hover { color: var(--accent); }
.poem-card .meta .who .title { color: var(--ink-2); }
.poem-card .meta .who .year {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-3);
  margin-left: 10px;
}
.poem-card .meta .tools { display: flex; gap: 4px; }
.poem-card .meta .tools button {
  width: 30px; height: 30px;
  display: grid; place-items: center;
  border-radius: 50%;
  color: var(--ink-3);
  transition: all .15s;
}
.poem-card .meta .tools button:hover { background: var(--paper-3); color: var(--ink); }
.poem-card .meta .tools button.fav.active { color: var(--alert); }

.quote-card {
  background: var(--paper-2);
  border: 1px solid var(--line);
  padding: 28px 32px 22px;
  margin: 14px auto;
  max-width: 760px;
  position: relative;
  font-family: var(--font-body);
}
body[data-theme='light'] .quote-card { background: #FBF6EA; }
.quote-card::before {
  content: '';
  position: absolute;
  inset: 8px;
  border: 1px dashed var(--line);
  pointer-events: none;
}
.quote-card .quoter {
  font-family: var(--font-display);
  font-size: 62px;
  line-height: .7;
  color: var(--accent);
  opacity: .35;
  position: absolute;
  top: 14px; left: 16px;
}
.quote-card .text {
  font-size: 23px;
  font-style: italic;
  line-height: 1.45;
  color: var(--ink);
  position: relative;
  padding-left: 36px;
  padding-right: 84px;
  text-wrap: pretty;
}
@media (max-width: 520px) {
  .quote-card .text { padding-right: 72px; font-size: 21px; }
}
.quote-card .attr {
  margin-top: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-left: 36px;
}
.quote-card .attr .name {
  font-family: var(--font-ui);
  font-size: 12px;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--ink);
}
.quote-card .attr .role {
  font-family: var(--font-body);
  font-style: italic;
  font-size: 14px;
  color: var(--ink-2);
}
.quote-card .stamp {
  position: absolute;
  top: 16px; right: 16px;
  width: 58px; height: 58px;
  border: 2px solid var(--stamp);
  border-radius: 50%;
  display: grid;
  place-items: center;
  transform: rotate(-12deg);
  color: var(--stamp);
  font-family: var(--font-ui);
  font-size: 8px;
  letter-spacing: .18em;
  text-align: center;
  opacity: .55;
  line-height: 1.1;
}

.frames-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin: 32px 0 40px;
}
.frame-thumb {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: #000;
  cursor: pointer;
  transition: transform .3s;
}
.frame-thumb img, .frame-thumb .ph {
  width: 100%; height: 100%; object-fit: cover;
  transition: transform .5s, filter .3s;
}
.frame-thumb:hover img, .frame-thumb:hover .ph { transform: scale(1.03); filter: brightness(1.05); }
.frame-thumb .caption {
  position: absolute;
  inset: auto 0 0 0;
  padding: 28px 14px 12px;
  background: linear-gradient(180deg, transparent 0%, rgba(0,0,0,.85) 100%);
  color: #EDE4D3;
  opacity: 0;
  transition: opacity .25s;
}
.frame-thumb:hover .caption { opacity: 1; }
.frame-thumb .caption .t {
  font-family: var(--font-display);
  font-size: 17px;
  line-height: 1.1;
}
.frame-thumb .caption .sub {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: #B8AB93;
  margin-top: 4px;
}

.frame-card {
  margin: 28px auto;
  max-width: 1120px;
  background: #0b0a09;
  border: 1px solid var(--line);
  position: relative;
}
.frame-card .reel {
  position: absolute;
  top: 0; bottom: 0; width: 22px;
  background: #0b0a09;
  pointer-events: none;
}
.frame-card .reel.left  { left: 0; }
.frame-card .reel.right { right: 0; }
.frame-card .reel::after {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(circle at 50% 14px, var(--paper) 2.5px, transparent 3px);
  background-size: 100% 28px;
}
.frame-card .stage { position: relative; padding: 0 22px; }
.frame-card .shot {
  aspect-ratio: 16 / 9;
  background: #000;
  overflow: hidden;
  position: relative;
}
.frame-card .shot img, .frame-card .shot .ph {
  width: 100%; height: 100%; object-fit: cover;
}
.frame-card .shot .slate {
  position: absolute;
  top: 16px; left: 16px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: .15em;
  color: #fff;
  background: rgba(0,0,0,.5);
  padding: 5px 10px;
  backdrop-filter: blur(6px);
  border-left: 2px solid var(--accent);
}
.frame-card .details {
  padding: 28px 44px 28px;
  color: #EDE4D3;
  background: #0b0a09;
  position: relative;
  z-index: 1;
}
.frame-card .details .title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 32px;
  flex-wrap: wrap;
}
.frame-card .details h2 {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: 40px;
  line-height: 1;
  margin: 0;
}
.frame-card .details .credits {
  font-family: var(--font-body);
  font-style: italic;
  color: #B8AB93;
  font-size: 16px;
}
.frame-card .details .credits .yr {
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 12px;
  margin-left: 10px;
  color: var(--accent);
}
.frame-card .details .why {
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.55;
  color: #D0C4A6;
  margin: 16px 0 0;
  max-width: 780px;
  text-wrap: pretty;
}
.frame-card .details .tags {
  display: flex; flex-wrap: wrap; gap: 4px;
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #2a2520;
}
.frame-card .details .tag {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .1em;
  text-transform: lowercase;
  color: #8a7a5c;
  padding: 4px 10px;
  border: 1px solid #2a2520;
  border-radius: 999px;
}
.frame-tools-mini { display: flex; gap: 6px; margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--line-2); }
.ft-btn { width: 36px; height: 36px; display: inline-flex; align-items: center; justify-content: center; background: transparent; border: 1px solid var(--line-2); color: var(--ink-2); cursor: pointer; transition: all .14s; font-size: 16px; padding: 0; border-radius: 2px; }
.ft-btn:hover { border-color: var(--accent); color: var(--accent); }
.ft-btn.on { color: var(--alert); border-color: var(--alert); }
.ft-btn.danger:hover { border-color: var(--alert); color: var(--alert); }
.ft-btn svg { width: 15px; height: 15px; }
.frame-card .nav-buttons { display: flex; justify-content: space-between; align-items: center; padding: 14px 22px; background: transparent; border-top: 1px solid rgba(255,255,255,.08); border-bottom: 1px solid rgba(255,255,255,.08); margin: 0; font-family: var(--font-ui); font-size: 11px; letter-spacing: .22em; text-transform: uppercase; }
.frame-card .nav-buttons .btn-nav { background: transparent; border: 0; color: #EDE4D3; font: inherit; cursor: pointer; padding: 4px 8px; }
.frame-card .nav-buttons .btn-nav:hover { color: var(--accent); }
.frame-card .nav-buttons .btn-nav:disabled { opacity: .35; cursor: not-allowed; }
.frame-card .nav-buttons .page-ind { font-family: var(--font-mono); font-size: 10px; color: #8A7F6E; letter-spacing: .3em; }
.frame-card .nav-buttons .btn-next { background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.15); color: #EDE4D3; }
.frame-card .nav-buttons .btn-next:hover { background: var(--accent); border-color: var(--accent); color: var(--paper); }

.ph {
  background: linear-gradient(135deg, var(--p1, #3a2828) 0%, var(--p2, #6b3228) 50%, var(--p3, #8a5030) 100%);
  position: relative;
}
.ph::after {
  content: '';
  position: absolute; inset: 0;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.7' numOctaves='2' seed='3'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .4 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
  opacity: .5;
  mix-blend-mode: overlay;
}

.alphabet {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 2px;
  margin: 24px 0 32px;
}
.alphabet button {
  width: 34px; height: 34px;
  display: grid; place-items: center;
  font-family: var(--font-display);
  font-size: 17px;
  color: var(--ink-2);
  border: 1px solid transparent;
  transition: all .15s;
}
.alphabet button:hover { color: var(--accent); }
.alphabet button.active {
  color: var(--accent);
  border-color: var(--accent);
}
.alphabet button:disabled { color: var(--ink-3); opacity: .3; cursor: default; }

.letter-group { margin: 40px 0; }
.letter-head {
  font-family: var(--font-display);
  font-size: 96px;
  line-height: .9;
  color: var(--accent);
  opacity: .7;
  margin: 0 0 4px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.letter-head .count {
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.poet-block { border-bottom: 1px solid var(--line-2); }
.poet-head {
  display: grid;
  grid-template-columns: 24px 1fr auto auto;
  align-items: center;
  gap: 16px;
  padding: 18px 4px;
  cursor: pointer;
  transition: background .15s;
}
.poet-head:hover { background: var(--paper-2); }
.poet-head .fav-btn {
  width: 20px; height: 20px;
  display: grid; place-items: center;
  color: var(--ink-3);
}
.poet-head .fav-btn.active { color: var(--alert); }
.poet-head .poet-name {
  font-family: var(--font-display);
  font-size: 22px;
  color: var(--ink);
}
.poet-head .poet-meta {
  font-family: var(--font-body);
  font-style: italic;
  color: var(--ink-2);
  font-size: 14px;
}
.poet-head .count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
}
.poet-head .chevron { color: var(--ink-3); transition: transform .25s; }
.poet-block.open .poet-head .chevron { transform: rotate(90deg); color: var(--accent); }

.poet-poems {
  padding: 8px 0 24px 40px;
  border-left: 1px solid var(--line);
  margin-left: 10px;
  display: grid;
  gap: 4px;
}
.poem-row {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding: 10px 14px;
  transition: background .15s;
  cursor: pointer;
  border-radius: 2px;
}
.poem-row:hover { background: var(--paper-2); }
.poem-row .n {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  width: 24px;
  flex: 0 0 auto;
}
.poem-row .poem-title {
  font-family: var(--font-body);
  font-size: 19px;
  color: var(--ink);
  flex: 0 0 auto;
  max-width: 280px;
}
.poem-row .excerpt {
  font-family: var(--font-body);
  font-style: italic;
  font-size: 16px;
  color: var(--ink-2);
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.poem-row .read {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--accent);
  opacity: 0;
  transition: opacity .15s;
}
.poem-row:hover .read { opacity: 1; }

.show-more-btn {
  display: block;
  margin: 14px auto 8px;
  padding: 10px 22px;
  background: transparent;
  color: var(--accent);
  border: 1px solid var(--line);
  border-radius: 2px;
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .22em;
  text-transform: uppercase;
  cursor: pointer;
  transition: background .15s, border-color .15s;
}
.show-more-btn:hover { background: var(--paper-2); border-color: var(--accent); }

.overlay {
  position: fixed; inset: 0;
  background: rgba(8,6,5,.86);
  backdrop-filter: blur(10px);
  display: grid;
  place-items: center;
  z-index: 100;
  padding: 40px;
  animation: fadeIn .25s ease;
}
body[data-theme='light'] .overlay { background: rgba(40,30,20,.5); }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.poem-modal {
  background: var(--paper);
  max-width: 720px;
  width: 100%;
  max-height: 90vh;
  overflow: auto;
  position: relative;
  border: 1px solid var(--line);
  padding: 60px 72px 56px;
  animation: slideUp .35s cubic-bezier(.2,.8,.2,1);
}
body[data-theme='light'] .poem-modal { background: #FBF6EA; }
@keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: none; } }

.poem-modal .close-x {
  position: absolute; top: 20px; right: 20px;
  width: 32px; height: 32px;
  display: grid; place-items: center;
  color: var(--ink-2);
  transition: color .15s;
}
.poem-modal .close-x:hover { color: var(--alert); }
.poem-modal .kicker {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .25em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 12px;
}
.poem-modal h2 {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: 46px;
  line-height: 1.05;
  margin: 0 0 8px;
}
.poem-modal .author-line {
  font-family: var(--font-body);
  font-style: italic;
  font-size: 20px;
  color: var(--ink-2);
  margin-bottom: 36px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--line);
}
.poem-modal .poem-text {
  font-family: var(--font-body);
  font-size: 23px;
  line-height: 1.6;
  color: var(--ink);
  white-space: pre-wrap;
}
.poem-modal .poem-text textarea {
  width: 100%; min-height: 220px; resize: vertical;
  padding: 12px 14px;
  border: 1px solid var(--line);
  background: var(--paper-2);
  font-family: var(--font-body);
  font-size: 17px;
  line-height: 1.55;
  color: var(--ink);
  outline: 0;
}
.poem-modal .poem-text textarea:focus { border-color: var(--accent); }
.poem-modal .modal-actions {
  display: flex;
  gap: 8px;
  margin-top: 40px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
  flex-wrap: wrap;
}

.frame-overlay {
  position: fixed; inset: 0;
  background: rgba(5,4,3,.85);
  backdrop-filter: blur(16px);
  z-index: 100;
  display: grid;
  grid-template-rows: 1fr auto;
  animation: fadeIn .25s;
}
.frame-overlay .x {
  position: absolute; top: 22px; right: 28px; z-index: 2;
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .25em;
  text-transform: uppercase;
  color: #EDE4D3;
  padding: 10px 18px;
  border: 1px solid #3a332d;
  background: rgba(0,0,0,.4);
}
.frame-overlay .big {
  display: grid;
  place-items: center;
  padding: 60px 80px 24px;
  overflow: hidden;
}
.frame-overlay .big img, .frame-overlay .big .ph {
  max-width: 100%; max-height: 100%;
  object-fit: contain;
  box-shadow: 0 40px 80px rgba(0,0,0,.6);
}
.frame-overlay .strip {
  background: rgba(8,6,5,.9);
  border-top: 1px solid #2a2520;
  padding: 20px 80px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 40px;
  color: #EDE4D3;
  flex-wrap: wrap;
}
.frame-overlay .strip .title {
  font-family: var(--font-display);
  font-size: 22px;
}
.frame-overlay .strip .credits {
  font-family: var(--font-body);
  font-style: italic;
  color: #B8AB93;
  font-size: 14px;
}
.frame-overlay .strip .why {
  font-family: var(--font-body);
  font-size: 14px;
  color: #B8AB93;
  max-width: 420px;
  line-height: 1.45;
}

.frame-add-box {
  background: var(--paper);
  border: 1px solid var(--line);
  max-width: 640px;
  width: 100%;
  padding: 48px 56px;
  position: relative;
  max-height: 90vh;
  overflow: auto;
}
body[data-theme='light'] .frame-add-box { background: #FBF6EA; }
.frame-add-box h3 {
  font-family: var(--font-display);
  font-weight: 400;
  font-size: 32px;
  margin: 0 0 6px;
}
.frame-add-box .sub {
  font-family: var(--font-body);
  font-style: italic;
  color: var(--ink-2);
  margin-bottom: 28px;
}
.frame-dropzone {
  border: 1px dashed var(--line);
  padding: 48px 24px;
  text-align: center;
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .22em;
  text-transform: uppercase;
  color: var(--ink-3);
  transition: all .15s;
  aspect-ratio: 16 / 9;
  display: grid;
  place-items: center;
  cursor: pointer;
  overflow: hidden;
}
.frame-dropzone.has-image { padding: 0; aspect-ratio: 16 / 9; }
.frame-dropzone.has-image img { width: 100%; height: 100%; object-fit: cover; }
.frame-dropzone:hover, .frame-dropzone.drag-over {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--paper-2);
}
.frame-dropzone .icon {
  font-size: 28px;
  margin-bottom: 10px;
}
.frame-add-box .field { display: grid; gap: 6px; margin-top: 18px; }
.frame-add-box .field label {
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .22em;
  text-transform: uppercase;
  color: var(--ink-2);
}
.frame-add-box .field input,
.frame-add-box .field textarea {
  padding: 10px 14px;
  border: 1px solid var(--line);
  font-family: var(--font-body);
  font-size: 16px;
  color: var(--ink);
  background: var(--paper-2);
  outline: 0;
  transition: border-color .15s;
}
.frame-add-box .field input:focus,
.frame-add-box .field textarea:focus { border-color: var(--accent); }
.frame-add-box .field textarea { resize: vertical; min-height: 80px; }
.frame-add-box .frame-add-actions {
  display: flex; gap: 8px;
  margin-top: 28px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}

.nav-buttons {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 24px;
  margin: 20px 0 40px;
}
.btn-nav {
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .22em;
  text-transform: uppercase;
  padding: 10px 20px;
  color: var(--ink-2);
  transition: color .15s;
}
.btn-nav:hover { color: var(--accent); }
.btn-nav:disabled { color: var(--ink-3); opacity: .3; cursor: default; }
.btn-next {
  padding: 12px 24px;
  background: var(--paper-3);
  color: var(--ink);
  border: 1px solid var(--line);
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .22em;
  text-transform: uppercase;
  transition: all .15s;
}
.btn-next:hover {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--paper);
}

.page-ind {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}

.footer {
  padding: 40px 0;
  margin-top: 60px;
  border-top: 1px solid var(--line);
  font-family: var(--font-ui);
  font-size: 10px;
  letter-spacing: .2em;
  text-transform: uppercase;
  color: var(--ink-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}
.footer .stat { display: flex; gap: 24px; }
.footer .stat strong {
  color: var(--ink);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.shuffling { animation: shuffleOut .45s ease forwards; }
@keyframes shuffleOut {
  0%   { opacity: 1; transform: translateY(0); filter: blur(0); }
  50%  { opacity: 0; transform: translateY(-8px); filter: blur(4px); }
  51%  { transform: translateY(8px); }
  100% { opacity: 1; transform: translateY(0); filter: blur(0); }
}

.loading-screen {
  min-height: 60vh;
  display: grid;
  place-items: center;
  font-family: var(--font-body);
  font-style: italic;
  color: var(--ink-2);
  font-size: 18px;
}
.loading-screen .spin {
  font-family: var(--font-display);
  font-size: 48px;
  color: var(--accent);
  animation: spin 2.2s linear infinite;
  display: inline-block;
  margin-right: 16px;
}
@keyframes spin { to { transform: rotate(360deg); } }

.back-btn {
  position: fixed;
  top: 14px;
  left: 12px;
  z-index: 60;
  width: 32px; height: 32px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: color-mix(in srgb, var(--paper-2) 72%, transparent);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: var(--ink-2);
  display: grid;
  place-items: center;
  transition: all .18s;
  animation: backIn .22s ease;
}
.back-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
  transform: translateX(-2px);
  background: var(--paper-2);
}
.back-btn svg { width: 14px; height: 14px; }
@keyframes backIn { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: translateX(0); } }

.toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--paper-3);
  border: 1px solid var(--accent);
  color: var(--ink);
  padding: 12px 22px;
  font-family: var(--font-ui);
  font-size: 11px;
  letter-spacing: .18em;
  text-transform: uppercase;
  z-index: 500;
  box-shadow: 0 12px 28px rgba(0,0,0,.5);
  animation: toastIn .25s ease;
}
@keyframes toastIn { from { opacity: 0; transform: translate(-50%, 10px); } to { opacity: 1; transform: translate(-50%, 0); } }

@media (max-width: 720px) {
  .masthead { grid-template-columns: 1fr; gap: 12px; text-align: center; }
  .masthead .issue { justify-content: center; }
  .masthead .theme-switch { justify-self: center; }
  .sections .tab { padding: 8px 20px 18px; font-size: 17px; }
  .poem-card .excerpt { padding: 32px 24px 20px; font-size: 22px; }
  .poem-card .meta { padding: 16px 24px; flex-wrap: wrap; }
  .quote-card { padding: 32px 24px; }
  .quote-card .text { font-size: 24px; padding-left: 20px; }
  .quote-card .attr { padding-left: 20px; }
  .frame-card .details { padding: 24px; }
  .frame-card .details h2 { font-size: 28px; }
  .poem-modal { padding: 40px 28px; }
  .poem-modal h2 { font-size: 30px; }
  .frame-overlay .big { padding: 40px 20px 10px; }
  .frame-overlay .strip { padding: 16px 20px; }
  .frame-add-box { padding: 28px 20px; }
  .hero { padding: 32px 0 24px; }
}

.hidden { display: none !important; }
.view { display: none; }
.view.active { display: block; }
</style>
</head>
<body data-theme="dark">
<div id="root"><div class="loading-screen"><span class="spin">✦</span>Сеанс начинается…</div></div>
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" crossorigin="anonymous"></script>
<script>
(function () {
'use strict';

const e = React.createElement;
const { useState, useEffect, useMemo, useRef, Fragment } = React;

// ═══════════ UTILS ═══════════
const copy = s => {
  try { navigator.clipboard?.writeText(s); toast('Скопировано'); }
  catch (err) { toast('Не удалось скопировать'); }
};
let toastTimer = null;
function toast(msg) {
  const old = document.querySelector('.toast');
  if (old) old.remove();
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 2200);
}
const phStyle = palette => palette ? { '--p1': palette[0], '--p2': palette[1], '--p3': palette[2] } : {};

function normalize(s) {
  return (s || '').toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9\s]/gi, ' ').trim();
}
function firstLetter(name) {
  const cleaned = (name || '').trim();
  const parts = cleaned.split(/\s+/);
  const last = parts[parts.length - 1] || cleaned;
  const ch = last.charAt(0).toUpperCase();
  return ch || '#';
}
function splitExcerpt(text, title) {
  if (!text) return [title || '[текст подгружается]'];
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l);
  return lines.slice(0, 4);
}

// ═══════════ ICONS ═══════════
const Icon = {
  search: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('circle', { cx:11, cy:11, r:7 }), e('path', { d:'m20 20-3.5-3.5' })),
  heart: f => e('svg', { viewBox:'0 0 24 24', fill: f?'currentColor':'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10Z' })),
  copy: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('rect', { x:9, y:9, width:11, height:11, rx:1 }), e('path', { d:'M5 15V5a1 1 0 0 1 1-1h10' })),
  open: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M7 17 17 7M9 7h8v8' })),
  refresh: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M20 4v6h-6M4 20v-6h6M20 10a8 8 0 0 0-14.9-3M4 14a8 8 0 0 0 14.9 3' })),
  download: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M12 4v12m0 0-4-4m4 4 4-4M5 20h14' })),
  plus: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M12 5v14M5 12h14' })),
  folder: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z' })),
  edit: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'m4 20 4-1 11-11-3-3L5 16l-1 4ZM14 6l3 3' })),
  trash: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M4 7h16M9 7V4h6v3M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13' })),
  chev: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'m9 6 6 6-6 6' })),
  x: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M6 6l12 12M18 6 6 18' })),
  arrowLeft: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M15 6l-6 6 6 6' })),
  sun: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('circle', { cx:12, cy:12, r:4 }),
    e('path', { d:'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4' })),
  moon: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('path', { d:'M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10Z' })),
  cog: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.5' },
    e('circle', { cx:12, cy:12, r:3 }),
    e('path', { d:'M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4' })),
  stop: () => e('svg', { viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:'1.6' },
    e('rect', { x:6, y:6, width:12, height:12, rx:1 })),
};

// ═══════════ DATA LOADER ═══════════
async function loadAll() {
  const [poemsRes, framesRes] = await Promise.all([
    fetch('/api/poems').then(r => r.json()).catch(() => []),
    fetch('/api/screenshots').then(r => r.json()).catch(() => []),
  ]);
  return { raw: poemsRes || [], frames: framesRes || [] };
}

function buildDataset(raw, frames) {
  const POEMS = [];
  const QUOTES = [];
  for (const r of raw) {
    const isQuote = r.type === 'quote' || (r.category && /цитат/i.test(r.category));
    const id = r.link || (r.author + '::' + r.title);
    if (isQuote) {
      const text = r.text || r.title || '';
      QUOTES.push({
        id,
        author: r.author || '—',
        role: r.source || r.role || r.category || '',
        kind: r.kind || 'автор',
        text,
        hay: normalize((r.author || '') + ' ' + (r.source || '') + ' ' + text),
      });
    } else {
      const excerpt = splitExcerpt(r.text, r.title);
      const fullLines = r.text
        ? r.text.split(/\r?\n/)
        : null;
      // Предвычисленный haystack для поиска — нормализуем один раз.
      const hay = normalize((r.author || '') + ' ' + (r.title || '') + ' ' +
        (r.text || excerpt.join(' ')));
      POEMS.push({
        id,
        author: r.author || '—',
        title: r.title || '[без названия]',
        year: r.year || null,
        category: r.category || '',
        excerpt,
        full: fullLines,
        link: r.link || '',
        hasText: !!r.text,
        hay,
      });
    }
  }

  const poetMap = new Map();
  for (const p of POEMS) {
    if (!poetMap.has(p.author)) {
      poetMap.set(p.author, { name: p.author, letter: firstLetter(p.author), poems: [] });
    }
    poetMap.get(p.author).poems.push(p);
  }
  const POETS = Array.from(poetMap.values()).map(p => ({
    ...p,
    count: p.poems.length,
    nameHay: normalize(p.name),
  })).sort((a, b) => a.name.localeCompare(b.name, 'ru'));

  // Счётчики категорий считаем только по стихам с текстом — иначе цифра
  // в фильтре расходится с фактическим числом карточек в рулетке.
  const usablePoems = POEMS.filter(p => p.hasText);
  const catCount = new Map();
  for (const p of usablePoems) {
    const c = (p.category || '').trim();
    if (!c) continue;
    catCount.set(c, (catCount.get(c) || 0) + 1);
  }
  const CATEGORIES = [
    { id: 'all', label: 'Всё', count: usablePoems.length + QUOTES.length },
    { id: 'fav', label: 'Избранное', count: 0 },
  ];
  if (QUOTES.length) CATEGORIES.push({ id: '__quotes', label: 'Цитаты', count: QUOTES.length });
  if (usablePoems.length) CATEGORIES.push({ id: '__poems', label: 'Только стихи', count: usablePoems.length });
  const sortedCats = Array.from(catCount.entries()).sort((a, b) => b[1] - a[1]);
  for (const [name, count] of sortedCats) {
    if (!/цитат/i.test(name)) CATEGORIES.push({ id: 'cat::' + name, label: name, count });
  }

  const FRAMES = (frames || []).map(f => ({
    id: f.id,
    title: f.film || '[без названия]',
    director: f.director || '',
    year: f.year || '',
    dp: f.dp || f.operator || '',
    img: f.file ? ('/img/' + encodeURIComponent(f.file)) : null,
    why: f.why || '',
    tags: Array.isArray(f.tags) ? f.tags : (f.tags ? String(f.tags).split(',').map(s => s.trim()) : []),
    note: f.note || '',
    palette: null,
  }));

  return { POEMS, QUOTES, POETS, CATEGORIES, FRAMES };
}

// ═══════════ MASTHEAD ═══════════
function Masthead({ theme, setTheme }) {
  const today = new Date().toLocaleDateString('ru-RU', {
    day: '2-digit', month: 'long', year: 'numeric',
  });
  const month = new Date().toLocaleDateString('ru-RU', { month: 'long' });
  return e('header', { className: 'masthead' },
    e('div', { className: 'issue' },
      e('span', null, '№ 01 · ' + month),
      e('span', { className: 'dot' }),
      e('span', null, today)),
    e('div', { className: 'brand' }, 'Сеанс'),
    e('div', { className: 'theme-switch' },
      e('button', { className: theme==='dark'?'active':'', onClick:()=>setTheme('dark'), title:'Тёмная' }, e(Icon.moon)),
      e('button', { className: theme==='light'?'active':'', onClick:()=>setTheme('light'), title:'Светлая' }, e(Icon.sun))));
}

// ═══════════ TAGLINE ═══════════
function Tagline() {
  return e('div', { className: 'tagline' },
    e('p', { className: 'lead' },
      'Не библиотека — ', e('em', null, 'рулетка для вдохновения')),
    e('p', { className: 'sub' },
      'Стих, цитата или кадр из кино — наугад, как карта из колоды. Не ищите — пусть случай выберет за вас.'));
}

// ═══════════ NAV ═══════════
function Sections({ section, setSection }) {
  return e('nav', { className: 'sections' },
    e('button', { className: 'tab ' + (section==='lit'?'active':''), onClick:()=>setSection('lit') },
      e('span', { className:'num' }, '— I —'), 'Литература'),
    e('button', { className: 'tab ' + (section==='cine'?'active':''), onClick:()=>setSection('cine') },
      e('span', { className:'num' }, '— II —'), 'Кино'));
}

function SubNav({ items, active, onChange }) {
  return e('div', { className: 'subnav' }, items.map(it =>
    e('button', { key: it.id, className: active===it.id?'active':'', onClick:()=>onChange(it.id) }, it.label)));
}

// ═══════════ POEM CARD ═══════════
function PoemCard({ poem, onOpen, fav, onFav, folio, shuffling, onAuthorClick }) {
  const excerpt = poem.excerpt && poem.excerpt.length ? poem.excerpt : [poem.title];
  const firstLine = excerpt[0] || '';
  const rest = excerpt.slice(1);
  const firstChar = firstLine.charAt(0);
  const isLetter = /\p{L}/u.test(firstChar);
  const useDrop = rest.length > 0 && firstLine.length >= 2 && isLetter;
  return e('article', { className: 'poem-card ' + (shuffling?'shuffling':'') },
    e('div', { className: 'colophon' },
      e('span', { className:'folio' }, 'стр. ' + folio),
      poem.category ? e('span', { className:'cat' }, '✦ ' + poem.category) : e('span', null, ''),
      e('span', { className:'folio' }, 'Отрывок')),
    e('div', { className: 'excerpt' + (useDrop ? '' : ' no-drop') },
      e('p', null,
        useDrop ? e('span', { className:'drop' }, firstLine.charAt(0)) : null,
        useDrop ? firstLine.slice(1) : firstLine),
      rest.map((line, i) => e('p', { key: i }, line || '\u00A0'))),
    e('div', { className: 'meta' },
      e('div', { className: 'who' },
        e('span', { className:'author', onClick: onAuthorClick }, poem.author),
        e('span', { className:'title' }, ' · «' + poem.title + '»'),
        poem.year ? e('span', { className:'year' }, poem.year) : null),
      e('div', { className: 'tools' },
        e('button', { className:'fav '+(fav?'active':''), onClick: onFav, title:'В избранное' }, e(Icon.heart, null, fav)),
        e('button', { onClick: () => copy((poem.full && poem.full.length ? poem.full.join('\n') : excerpt.join('\n'))), title:'Скопировать отрывок' }, e(Icon.copy)),
        e('button', { onClick: onOpen, title:'Читать целиком' }, e(Icon.open)))));
}

function QuoteCard({ quote, fav, onFav, shuffling }) {
  const kind = (quote.kind || quote.role || 'ЦИТАТА').toString().toUpperCase().slice(0, 14);
  return e('article', { className: 'quote-card ' + (shuffling?'shuffling':'') },
    e('div', { className: 'quoter' }, '“'),
    e('div', { className: 'stamp' }, 'СЕАНС', e('br'), kind),
    e('p', { className: 'text' }, quote.text),
    e('div', { className: 'attr' },
      e('div', null,
        e('div', { className:'name' }, quote.author),
        quote.role ? e('div', { className:'role' }, quote.role) : null),
      e('div', { style:{display:'flex', gap:4} },
        e('button', { className:'fav', onClick: onFav, style:{ width:34, height:34, display:'grid', placeItems:'center', color: fav?'var(--alert)':'var(--ink-3)' } }, e(Icon.heart, null, fav)),
        e('button', { onClick: () => copy('«'+quote.text+'» — '+quote.author), style:{ width:34, height:34, display:'grid', placeItems:'center', color:'var(--ink-3)' } }, e(Icon.copy)))));
}

// ═══════════ HOME (rulette + library) ═══════════
const CARDS_PER_PAGE = 5;

function shuffleArray(src) {
  const arr = src.slice();
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
  }
  return arr;
}

function Roulette({ data, openPoem, favs, toggleFav, onAuthorClick, lockedCategory }) {
  const { POEMS, QUOTES, CATEGORIES } = data;
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [categoryState, setCategoryState] = useState(lockedCategory || 'all');
  const category = lockedCategory || categoryState;
  const setCategory = (v) => { if (!lockedCategory) setCategoryState(v); };
  const [idx, setIdx] = useState(0);
  const [shuffleToken, setShuffleToken] = useState(() => Math.random());
  const [shuffling, setShuffling] = useState(false);
  const filterRef = useRef(null);
  const [updating, setUpdating] = useState(false);
  const [updateStage, setUpdateStage] = useState('');
  const statusRef = useRef(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 180);
    return () => clearTimeout(t);
  }, [query]);

  const deckBase = useMemo(() => {
    if (category === '__quotes') return QUOTES;
    // В избранном показываем всё отмеченное, даже если текст не подгрузился.
    if (category === 'fav') return [...POEMS, ...QUOTES];
    // Для рулетки берём только стихи с текстом — без него карточка
    // деградирует до одного заголовка. В библиотеке такие записи остаются:
    // там можно открыть модалку и вписать текст вручную через ✏️.
    const usablePoems = POEMS.filter(p => p.hasText);
    if (category === '__poems') return usablePoems;
    if (category && category.startsWith('cat::')) {
      const name = category.slice(5);
      return usablePoems.filter(p => p.category === name);
    }
    return [...usablePoems, ...QUOTES];
  }, [category, POEMS, QUOTES]);

  const deckWithQuery = useMemo(() => {
    if (!debouncedQuery.trim()) return deckBase;
    const q = normalize(debouncedQuery);
    return deckBase.filter(r => (r.hay || '').includes(q));
  }, [deckBase, debouncedQuery]);

  const shuffledBase = useMemo(() => shuffleArray(deckWithQuery), [deckWithQuery, shuffleToken]);
  const shuffled = useMemo(() => {
    if (category === 'fav') return shuffledBase.filter(r => favs.has(r.id));
    return shuffledBase;
  }, [shuffledBase, category, favs]);
  const safeIdx = shuffled.length ? idx % shuffled.length : 0;
  const visible = useMemo(() => {
    if (!shuffled.length) return [];
    const out = [];
    const take = Math.min(CARDS_PER_PAGE, shuffled.length);
    for (let i = 0; i < take; i++) out.push(shuffled[(safeIdx + i) % shuffled.length]);
    return out;
  }, [shuffled, safeIdx]);

  const shuffle = () => {
    if (!shuffled.length) return;
    setShuffling(true);
    setTimeout(() => {
      setShuffleToken(Math.random());
      setIdx(0);
      setShuffling(false);
    }, 230);
  };

  const step = (dir) => {
    if (!shuffled.length) return;
    setIdx(i => {
      const next = i + dir * CARDS_PER_PAGE;
      return ((next % shuffled.length) + shuffled.length) % shuffled.length;
    });
  };

  const runUpdate = async () => {
    if (updating) return;
    try {
      const r = await fetch('/api/update', { method:'POST' });
      if (!r.ok) { const d = await r.json().catch(()=>({})); toast(d.error || 'Не получилось запустить'); return; }
      setUpdating(true);
      setUpdateStage('начинаем…');
      statusRef.current = setInterval(async () => {
        const s = await fetch('/api/update/status').then(r=>r.json()).catch(()=>null);
        if (!s) return;
        setUpdateStage(s.stage || '');
        if (!s.running) {
          clearInterval(statusRef.current);
          statusRef.current = null;
          setUpdating(false);
          toast(s.ok === false ? ('Ошибка: ' + (s.error||'')) : 'База обновлена — перезагрузите');
        }
      }, 1500);
    } catch (err) { toast('Ошибка запроса'); }
  };

  const runDownloadTexts = async () => {
    if (updating) return;
    try {
      const r = await fetch('/api/download-texts', { method:'POST' });
      if (!r.ok) { const d = await r.json().catch(()=>({})); toast(d.error || 'Не получилось'); return; }
      setUpdating(true); setUpdateStage('качаю тексты…');
      statusRef.current = setInterval(async () => {
        const s = await fetch('/api/update/status').then(r=>r.json()).catch(()=>null);
        if (!s) return;
        setUpdateStage(s.stage || '');
        if (!s.running) {
          clearInterval(statusRef.current);
          statusRef.current = null;
          setUpdating(false);
          toast('Готово — перезагрузите');
        }
      }, 1500);
    } catch (err) { toast('Ошибка запроса'); }
  };

  const stopUpdate = async () => {
    await fetch('/api/stop', { method:'POST' }).catch(()=>{});
    toast('Останавливаю…');
  };

  const openFolder = () => fetch('/api/open-data-folder').then(() => toast('Папка открыта'));

  useEffect(() => () => { if (statusRef.current) clearInterval(statusRef.current); }, []);
  useEffect(() => { setIdx(0); }, [category]);

  if (!shuffled.length) {
    return e(Fragment, null,
      e('div', { className:'hero' },
        e('div', { className:'kicker' }, 'Литературная рулетка'),
        e('h1', null, 'Случайный ', e('em', null, 'отрывок')),
        e('p', { className:'lede' }, category === 'fav'
          ? 'В избранном пока ничего нет — нажмите ♥ у любого отрывка.'
          : 'База пуста — нажмите «Обновить», чтобы подтянуть тексты.')),
      e('div', { className:'actions' },
        category === 'fav'
          ? e('button', { className:'btn', onClick: () => setCategory('all') }, '← Вернуться ко всем отрывкам')
          : e('button', { className:'btn primary', onClick: runUpdate, disabled: updating },
              e('span', { className:'glyph' }, '↻'), updating ? (updateStage || 'работаю…') : 'Обновить базу')));
  }

  const categoriesWithFav = CATEGORIES.map(c => c.id==='fav' ? {...c, count: favs.size} : c);
  const activeCatLabel = (categoriesWithFav.find(c => c.id === category) || {}).label || 'Всё';

  const selectCategory = (id) => {
    setCategory(id);
    setIdx(0);
    if (filterRef.current) filterRef.current.open = false;
  };

  const renderCard = (item, i) => {
    const folio = String(((safeIdx + i) % Math.max(shuffled.length, 1)) + 1).padStart(3, '0');
    const isPoem = item && Array.isArray(item.excerpt);
    return isPoem
      ? e(PoemCard, {
          key: item.id + '::' + i,
          poem: item,
          onOpen: () => openPoem(item),
          fav: favs.has(item.id),
          onFav: () => toggleFav(item.id),
          folio,
          shuffling: shuffling && i === 0,
          onAuthorClick: () => onAuthorClick(item.author),
        })
      : e(QuoteCard, {
          key: item.id + '::' + i,
          quote: item,
          fav: favs.has(item.id),
          onFav: () => toggleFav(item.id),
          shuffling: shuffling && i === 0,
        });
  };

  const pageFrom = safeIdx + 1;
  const pageTo = Math.min(safeIdx + CARDS_PER_PAGE, shuffled.length);

  const isFav = lockedCategory === 'fav';
  return e(Fragment, null,
    e('div', { className:'hero' },
      e('div', { className:'kicker' }, isFav ? 'Ваша коллекция' : 'Литературная рулетка'),
      e('h1', null, isFav ? 'Избранные ' : 'Случайный ', e('em', null, isFav ? 'строки' : 'отрывок')),
      e('p', { className:'lede' }, isFav
        ? (shuffled.length + ' сохранено — стихи, цитаты, строки')
        : ((POEMS.length + QUOTES.length).toLocaleString('ru-RU') + ' стихов, цитат, строк — ' + CARDS_PER_PAGE + ' в каждый приход.'))),
    e('div', { className:'toolbar' },
      e('div', { className:'search-box' }, e(Icon.search),
        e('input', { placeholder: isFav ? 'Поиск среди избранного…' : 'Поиск среди отрывков…', value: query, onChange: ev => setQuery(ev.target.value) }),
        e('span', { className:'shortcut' }, '⌘K')),
      !lockedCategory && e('details', { className:'filter-drawer', ref: filterRef },
        e('summary', null,
          e('span', { className:'summary-left' },
            'Категория',
            e('span', { className:'active-chip' }, activeCatLabel, category === 'fav' ? (' · ' + favs.size) : '')),
          e('span', { className:'caret' }, '▾')),
        e('div', { className:'filters' }, categoriesWithFav.map(c =>
          e('button', { key: c.id, className: 'chip '+(category===c.id?'active':''), onClick: () => selectCategory(c.id) },
            c.label, e('span', { className:'c' }, c.count)))))),
    e('div', { className:'actions' },
      e('button', { className:'btn primary', onClick: shuffle }, e('span', { className:'glyph' }, '✦'), 'Перемешать'),
      e('details', { className:'overflow-menu' },
        e('summary', { className:'btn ghost' }, e(Icon.cog), 'Настройки'),
        e('div', { className:'overflow-panel' },
          e('div', { className:'overflow-head' }, 'база отрывков'),
          e('button', { className:'overflow-item', onClick: runUpdate, disabled: updating },
            e(Icon.refresh), e('span', { className:'l' },
              e('span', { className:'t' }, updating ? (updateStage || 'работаю…') : 'Обновить с сайта'),
              e('span', { className:'h' }, 'подтянуть свежие'))),
          e('button', { className:'overflow-item', onClick: runDownloadTexts, disabled: updating },
            e(Icon.download), e('span', { className:'l' },
              e('span', { className:'t' }, 'Скачать полные тексты'),
              e('span', { className:'h' }, 'wayback · wikisource'))),
          updating && e('button', { className:'overflow-item', onClick: stopUpdate },
            e(Icon.stop), e('span', { className:'l' },
              e('span', { className:'t' }, 'Остановить'),
              e('span', { className:'h' }, 'сохранить частичное'))),
          e('div', { className:'overflow-head' }, 'служебные'),
          e('button', { className:'overflow-item', onClick: openFolder },
            e(Icon.folder), e('span', { className:'l' },
              e('span', { className:'t' }, 'Открыть папку данных'),
              e('span', { className:'h' }, 'csv, json, бэкап')))))),
    e('div', { className:'status-bar' },
      'Показано ', e('span', null, pageFrom + '–' + pageTo), ' из ', e('span', null, shuffled.length),
      updating ? (' · ' + updateStage) : ''),
    e('div', { className:'card-stack' }, visible.map(renderCard)),
    e('div', { className:'nav-buttons' },
      e('button', { className:'btn-nav', onClick: () => step(-1) }, '← Предыдущие'),
      e('span', { className:'page-ind' },
        String(pageFrom).padStart(3, '0'), '–', String(pageTo).padStart(3, '0'), ' / ', String(shuffled.length).padStart(3, '0')),
      e('button', { className:'btn-next', onClick: () => step(1) }, 'Следующие →')));
}

// ═══════════ LIBRARY ═══════════
const POEMS_PER_BLOCK = 20;
const POETS_INITIAL_PAGE = 40;

function Library({ data, openPoem, favs, toggleFav, prefillAuthor, clearPrefill }) {
  const { POETS } = data;
  const [openPoet, setOpenPoet] = useState(prefillAuthor || null);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [activeLetter, setActiveLetter] = useState(null);
  // Какие блоки "развёрнуты полностью" — показаны все стихи, а не первые 20.
  const [expandedPoets, setExpandedPoets] = useState(() => new Set());
  // Лимит отображаемых поэтов при поиске — можно нарастить кнопкой.
  const [shownPoetsLimit, setShownPoetsLimit] = useState(POETS_INITIAL_PAGE);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  // Меняется запрос/фильтр — сбрасываем раскрытия и лимиты.
  useEffect(() => {
    setExpandedPoets(new Set());
    setShownPoetsLimit(POETS_INITIAL_PAGE);
  }, [debouncedQuery, activeLetter]);

  useEffect(() => {
    if (prefillAuthor) {
      setOpenPoet(prefillAuthor);
      setTimeout(() => {
        const el = document.querySelector('[data-poet="' + CSS.escape(prefillAuthor) + '"]');
        if (el) el.scrollIntoView({ behavior:'smooth', block:'center' });
      }, 80);
      clearPrefill && clearPrefill();
    }
  }, [prefillAuthor]);

  const alphabet = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ'.split('');
  const availLetters = new Set(POETS.map(p => p.letter));

  // p.hay предвычислен в data-загрузчике (normalize title + full text).
  const poemMatches = (pp, q) => (pp.hay || '').includes(q);

  const filtered = useMemo(() => {
    const q = normalize(debouncedQuery);
    return POETS.filter(p => {
      if (activeLetter && p.letter !== activeLetter) return false;
      if (!q) return true;
      if ((p.nameHay || '').includes(q)) return true;
      return p.poems.some(pp => poemMatches(pp, q));
    });
  }, [POETS, debouncedQuery, activeLetter]);

  const q = normalize(debouncedQuery);
  // При поиске — обрезаем список поэтов до лимита. Без поиска — показываем всех.
  const visiblePoets = useMemo(() => {
    if (!q) return filtered;
    return filtered.slice(0, shownPoetsLimit);
  }, [filtered, q, shownPoetsLimit]);
  const hiddenPoetsCount = q ? Math.max(0, filtered.length - visiblePoets.length) : 0;

  const byLetter = useMemo(() => {
    const m = {};
    for (const p of visiblePoets) (m[p.letter] = m[p.letter] || []).push(p);
    return m;
  }, [visiblePoets]);

  const letters = Object.keys(byLetter).sort();

  return e(Fragment, null,
    e('div', { className:'hero hero-lg' },
      e('div', { className:'kicker' }, 'Полный каталог'),
      e('h1', null, 'Библиотека ', e('em', null, 'поэтов')),
      e('p', { className:'lede' }, POETS.length + ' поэтов · ' +
        POETS.reduce((s,p)=>s+p.count,0).toLocaleString('ru-RU') + ' стихов и цитат')),
    e('div', { className:'toolbar' },
      e('div', { className:'search-box' }, e(Icon.search),
        e('input', { placeholder:'Поиск по фамилии поэта или строке стихотворения…', value: query, onChange: ev => setQuery(ev.target.value) })),
      e('div', { className:'alphabet' }, alphabet.map(l =>
        e('button', { key:l, disabled: !availLetters.has(l),
          className: activeLetter===l?'active':'',
          onClick: () => setActiveLetter(activeLetter===l?null:l) }, l)))),
    letters.map(l => e('section', { className:'letter-group', key: l },
      e('header', { className:'letter-head' },
        e('span', null, l),
        e('span', { className:'count' }, byLetter[l].length + ' ' + declPoets(byLetter[l].length))),
      byLetter[l].map(poet => {
        // При поиске блок всегда раскрыт; без поиска — по клику.
        const isOpen = q ? true : openPoet === poet.name;
        // Если запрос не совпал с именем, показываем только совпавшие стихи.
        const nameHit = q && (poet.nameHay || '').includes(q);
        let matched = poet.poems;
        if (q && !nameHit) matched = poet.poems.filter(p => poemMatches(p, q));
        const hitCount = q && !nameHit ? matched.length : null;
        // По умолчанию режем до POEMS_PER_BLOCK, кнопка раскрывает всё.
        const fullyOpen = expandedPoets.has(poet.name);
        const shownPoems = (isOpen && !fullyOpen && matched.length > POEMS_PER_BLOCK)
          ? matched.slice(0, POEMS_PER_BLOCK)
          : matched;
        const hiddenInBlock = matched.length - shownPoems.length;
        const expandBlock = () => setExpandedPoets(prev => {
          const next = new Set(prev); next.add(poet.name); return next;
        });
        return e('div', { key: poet.name, 'data-poet': poet.name, className: 'poet-block ' + (isOpen?'open':'') },
          e('div', { className:'poet-head', onClick: () => !q && setOpenPoet(isOpen ? null : poet.name) },
            e('button', { className:'fav-btn '+(favs.has('author::'+poet.name)?'active':''),
              onClick: ev => { ev.stopPropagation(); toggleFav('author::'+poet.name); } },
              e(Icon.heart, null, favs.has('author::'+poet.name))),
            e('div', null,
              e('span', { className:'poet-name' }, poet.name),
              poet.years ? e('span', { className:'poet-meta', style:{marginLeft:12} }, poet.years) : null),
            e('span', { className:'count' }, hitCount !== null
              ? (hitCount + ' из ' + poet.count)
              : (poet.count + ' стихов')),
            !q && e('span', { className:'chevron' }, e(Icon.chev))),
          isOpen && shownPoems.length > 0 && e('div', { className:'poet-poems' },
            shownPoems.map((p, i) => e('div', { key: p.id, className:'poem-row',
              onClick: () => openPoem(p) },
              e('span', { className:'n' }, String(i+1).padStart(2, '0')),
              e('span', { className:'poem-title' }, p.title),
              e('span', { className:'excerpt' }, (p.excerpt || []).join(' ').slice(0, 120)),
              e('span', { className:'read' }, 'читать →'))),
            hiddenInBlock > 0 && e('button', {
              className:'show-more-btn',
              onClick: expandBlock,
            }, 'Показать ещё ' + hiddenInBlock)),
          isOpen && shownPoems.length === 0 && e('div', { className:'poet-poems', style:{ fontStyle:'italic', color:'var(--ink-3)', padding:'16px 14px' } },
            '— тексты подгружаются —'));
      }))),
    hiddenPoetsCount > 0 && e('div', { style:{textAlign:'center', padding:'40px 20px'} },
      e('button', {
        className:'show-more-btn',
        onClick: () => setShownPoetsLimit(v => v + POETS_INITIAL_PAGE),
      }, 'Показать ещё ' + Math.min(POETS_INITIAL_PAGE, hiddenPoetsCount) + ' поэтов (всего ' + hiddenPoetsCount + ' скрыто)')),
    filtered.length === 0 && e('div', { style:{textAlign:'center', padding:'60px 20px', fontFamily:'var(--font-body)', fontStyle:'italic', color:'var(--ink-3)', fontSize:18} },
      'Ничего не найдено'));
}

function declPoets(n) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'поэт';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'поэта';
  return 'поэтов';
}

// ═══════════ CINEMA ═══════════
function Cinema({ data, subSection, setSubSection, favs, toggleFav, onFrameChanged }) {
  const FRAMES = useMemo(() => {
    if (subSection === 'favorites') return data.FRAMES.filter(f => favs.has(f.id));
    return data.FRAMES;
  }, [data.FRAMES, subSection, favs]);
  const [current, setCurrent] = useState(0);
  const [history, setHistory] = useState([]);
  const [shuffling, setShuffling] = useState(false);
  const [zoomed, setZoomed] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editingFrame, setEditingFrame] = useState(null);
  const [query, setQuery] = useState('');

  const pickRandom = (exclude) => {
    if (FRAMES.length <= 1) return 0;
    let n; do { n = Math.floor(Math.random() * FRAMES.length); } while (n === exclude);
    return n;
  };

  const shuffle = () => {
    if (!FRAMES.length) return;
    setShuffling(true);
    setTimeout(() => {
      setCurrent(c => pickRandom(c));
      setHistory([]);
      setShuffling(false);
    }, 230);
  };

  const goNext = () => {
    if (FRAMES.length <= 1) return;
    setHistory(h => [...h, current]);
    setCurrent(c => pickRandom(c));
  };

  const goPrev = () => {
    if (!FRAMES.length) return;
    if (history.length === 0) {
      setCurrent(c => (c - 1 + FRAMES.length) % FRAMES.length);
      return;
    }
    const last = history[history.length - 1];
    setCurrent(last);
    setHistory(h => h.slice(0, -1));
  };

  // Перемешиваем кадры при ПЕРВОМ монтировании компонента и при явном клике
  // на подтаб «Галерея» (см. обёртку onGallerySubNav ниже). Возврат через
  // кнопку «Назад» не триггерит новую перестановку — порядок сохраняется.
  const [galleryShuffleToken, setGalleryShuffleToken] = useState(() => Math.random());
  const shuffledFrames = useMemo(
    () => shuffleArray(FRAMES),
    [FRAMES, galleryShuffleToken]
  );

  const filteredFrames = useMemo(() => {
    if (!query.trim()) return shuffledFrames;
    const q = normalize(query);
    return shuffledFrames.filter(f => {
      const hay = normalize((f.title||'') + ' ' + (f.director||'') + ' ' + (f.dp||'') + ' ' + (f.tags||[]).join(' ') + ' ' + (f.why||''));
      return hay.includes(q);
    });
  }, [shuffledFrames, query]);

  const deleteFrame = async (id) => {
    if (!confirm('Удалить кадр?')) return;
    const r = await fetch('/api/screenshots/' + id, { method:'DELETE' });
    if (r.ok) { toast('Кадр удалён'); onFrameChanged(); }
    else toast('Не удалось удалить');
  };

  const cineItems = [
    {id:'roulette',label:'Рулетка'},
    {id:'gallery',label:'Галерея'},
    {id:'favorites',label:'♥ Избранное' + (data.FRAMES.filter(f=>favs.has(f.id)).length ? ' · ' + data.FRAMES.filter(f=>favs.has(f.id)).length : '')},
  ];

  // onChange для подтабов — при явном клике на «Галерею» перетасовываем.
  // Возврат через глобальную кнопку «Назад» зовёт setSubSection напрямую,
  // минуя эту обёртку, поэтому порядок сохраняется.
  const onSubNavChange = (next) => {
    if (next === 'gallery' && subSection !== 'gallery') {
      setGalleryShuffleToken(Math.random());
    }
    setSubSection(next);
  };

  if (!FRAMES.length) {
    const isFavView = subSection === 'favorites';
    return e(Fragment, null,
      e(SubNav, { items: cineItems, active: subSection, onChange: onSubNavChange }),
      e('div', { className:'hero hero-lg' },
        e('div', { className:'kicker' }, isFavView ? 'Ваша коллекция' : 'Визуальная рулетка'),
        e('h1', null, isFavView ? 'Избранные ' : 'Эталонные ', e('em', null, 'кадры')),
        e('p', { className:'lede' }, isFavView
          ? 'В избранном пока нет кадров — нажмите ♥ у любого кадра.'
          : 'Пока в коллекции нет кадров. Добавьте первый.')),
      e('div', { className:'actions' },
        isFavView
          ? e('button', { className:'btn', onClick: () => setSubSection('gallery') }, '← Перейти в галерею')
          : e('button', { className:'btn primary', onClick: () => setAddOpen(true) }, e(Icon.plus), 'Добавить кадр')),
      addOpen && e(AddFrameOverlay, { onClose: () => setAddOpen(false), onSaved: onFrameChanged, initial: null }));
  }

  const safeCurrent = Math.min(current, FRAMES.length - 1);
  const f = FRAMES[safeCurrent];

  const rouletteView = e(Fragment, null,
    e('div', { className:'hero hero-lg', style:{ padding:'28px 0 14px' } },
      e('div', { className:'kicker' }, 'Визуальная рулетка'),
      e('h1', null, 'Эталонные ', e('em', null, 'кадры'))),
    e('article', { className:'frame-card '+(shuffling?'shuffling':'') },
      e('div', { className:'reel left' }),
      e('div', { className:'reel right' }),
      e('div', { className:'stage' },
        e('div', { className:'shot', onClick: () => setZoomed(f), style:{cursor:'zoom-in'} },
          f.img ? e('img', { src:f.img, alt:f.title })
                 : e('div', { className:'ph', style: phStyle(f.palette) }),
          e('div', { className:'slate' },
            'REEL ', String(safeCurrent+1).padStart(2,'0'),
            f.year?(' · '+f.year):'', f.dp?(' · '+f.dp):''))),
      e('div', { className:'nav-buttons' },
        e('button', { className:'btn-nav', onClick: goPrev }, '← Предыдущий'),
        e('span', { className:'page-ind' }, String(safeCurrent+1).padStart(2,'0'), ' / ', String(FRAMES.length).padStart(2,'0')),
        e('button', { className:'btn-next', onClick: goNext }, 'Следующий →')),
      e('div', { className:'details' },
        e('div', { className:'title-row' },
          e('h2', null, f.title),
          e('div', { className:'credits' }, f.director, f.year ? e('span', { className:'yr' }, f.year) : null)),
        f.why ? e('p', { className:'why' }, f.why) : null,
        f.tags && f.tags.length ? e('div', { className:'tags' },
          f.tags.map((t, i) => e('span', { key:i, className:'tag' }, t))) : null,
        e('div', { className:'frame-tools-mini' },
          e('button', { title: favs.has(f.id)?'В избранном':'В избранное',
            className: favs.has(f.id)?'ft-btn on':'ft-btn', onClick: () => toggleFav(f.id) }, favs.has(f.id)?'♥':'♡'),
          e('button', { title:'Редактировать', className:'ft-btn', onClick: () => setEditingFrame(f) }, e(Icon.edit)),
          e('button', { title:'Копировать описание', className:'ft-btn', onClick: () => copy(f.why || '') }, e(Icon.copy)),
          e('button', { title:'Удалить', className:'ft-btn danger', onClick: () => deleteFrame(f.id) }, e(Icon.trash))))),
    e('div', { className:'actions', style:{ margin:'20px auto 0', justifyContent:'center' } },
      e('button', { className:'btn', onClick: () => setAddOpen(true) }, e(Icon.plus), 'Добавить кадр')));

  const galleryView = e(Fragment, null,
    e('div', { className:'hero hero-lg' },
      e('div', { className:'kicker' }, 'Коллекция'),
      e('h1', null, 'Галерея ', e('em', null, 'кадров')),
      e('p', { className:'lede' }, FRAMES.length + ' кадров · поиск по фильму, режиссёру, тегам')),
    e('div', { className:'toolbar' },
      e('div', { className:'search-box' }, e(Icon.search),
        e('input', { placeholder:'Поиск по фильму, режиссёру, тегам…', value: query, onChange: ev => setQuery(ev.target.value) })),
      e('div', { className:'actions' },
        e('button', { className:'btn primary', onClick: () => { setSubSection('roulette'); shuffle(); } },
          e('span', { className:'glyph' }, '✦'), 'Рулетка'),
        e('button', { className:'btn', onClick: () => setAddOpen(true) }, e(Icon.plus), 'Добавить кадр'))),
    e('div', { className:'frames-grid' }, filteredFrames.map((f, i) =>
      e('div', { key: f.id, className:'frame-thumb',
        onClick: () => {
          const idx = FRAMES.findIndex(x => x.id===f.id);
          setCurrent(idx >= 0 ? idx : 0);
          setSubSection('roulette');
          // После перехода — скроллим так, чтобы верх карточки был чуть ниже
          // шапки. Картинка + описание попадают в кадр целиком, теги уходят
          // вниз за пределы viewport — это ок.
          setTimeout(() => {
            const el = document.querySelector('.frame-card');
            if (!el) return;
            const r = el.getBoundingClientRect();
            const y = (window.scrollY || window.pageYOffset || 0) + r.top - 40;
            window.scrollTo({ top: y, behavior: 'smooth' });
          }, 60);
        } },
        f.img ? e('img', { src:f.img, alt:f.title })
               : e('div', { className:'ph', style: phStyle(f.palette) }),
        e('div', { className:'caption' },
          e('div', { className:'t' }, f.title),
          e('div', { className:'sub' }, f.director, f.year?(' · '+f.year):''))))),
    filteredFrames.length === 0 && e('div', { style:{textAlign:'center', padding:'60px 20px', fontFamily:'var(--font-body)', fontStyle:'italic', color:'var(--ink-3)', fontSize:18} },
      'Ничего не найдено'));

  return e('div', { className:'view active' },
    e(SubNav, { items: cineItems, active: subSection, onChange: onSubNavChange }),
    subSection === 'roulette' ? rouletteView : galleryView,
    zoomed && e('div', { className:'frame-overlay', onClick: ev => { if (ev.target.classList.contains('frame-overlay')) setZoomed(null); } },
      e('button', { className:'x', onClick: () => setZoomed(null) }, 'Закрыть ×'),
      e('div', { className:'big' },
        zoomed.img ? e('img', { src:zoomed.img })
                   : e('div', { className:'ph', style:{ ...phStyle(zoomed.palette), width:'min(1400px,90vw)', aspectRatio:'16/9' } })),
      e('div', { className:'strip' },
        e('div', null,
          e('div', { className:'title' }, zoomed.title),
          e('div', { className:'credits' }, zoomed.director, zoomed.year?(' · '+zoomed.year):'', zoomed.dp?(' · DP '+zoomed.dp):'')),
        zoomed.why ? e('div', { className:'why' }, zoomed.why) : null)),
    addOpen && e(AddFrameOverlay, { onClose: () => setAddOpen(false), onSaved: onFrameChanged, initial: null }),
    editingFrame && e(AddFrameOverlay, { onClose: () => setEditingFrame(null), onSaved: onFrameChanged, initial: editingFrame }));
}

// ═══════════ ADD / EDIT FRAME ═══════════
function AddFrameOverlay({ onClose, onSaved, initial }) {
  const [drag, setDrag] = useState(false);
  const [imageData, setImageData] = useState(null);
  const [filename, setFilename] = useState(initial ? initial.img ? initial.img.split('/').pop() : '' : '');
  const [film, setFilm] = useState(initial?.title || '');
  const [director, setDirector] = useState(initial?.director || '');
  const [year, setYear] = useState(initial?.year || '');
  const [why, setWhy] = useState(initial?.why || '');
  const [tags, setTags] = useState((initial?.tags || []).join(', '));
  const [saving, setSaving] = useState(false);
  const fileRef = useRef(null);

  const handleFile = (file) => {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) { toast('Файл больше 20 МБ'); return; }
    const reader = new FileReader();
    reader.onload = () => {
      setImageData(reader.result);
      setFilename(file.name);
    };
    reader.readAsDataURL(file);
  };

  const save = async () => {
    if (!film.trim()) { toast('Укажите фильм'); return; }
    if (!imageData && !initial) { toast('Добавьте изображение'); return; }
    setSaving(true);
    try {
      const payload = {
        film: film.trim(),
        director: director.trim(),
        year: year.trim(),
        why: why.trim(),
        tags: tags.split(/[,\n]/).map(s=>s.trim()).filter(Boolean),
      };
      let r;
      if (initial) {
        r = await fetch('/api/screenshots/' + initial.id, {
          method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
        });
      } else {
        payload.image_base64 = imageData;
        payload.original_filename = filename || 'image.jpg';
        r = await fetch('/api/screenshots', {
          method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
        });
      }
      const j = await r.json();
      if (j.ok) { toast(initial?'Кадр обновлён':'Кадр добавлен'); onSaved(); onClose(); }
      else toast('Ошибка: ' + (j.error || ''));
    } catch (err) { toast('Ошибка сохранения'); }
    finally { setSaving(false); }
  };

  return e('div', { className:'overlay', onClick: ev => { if (ev.target.classList.contains('overlay')) onClose(); } },
    e('div', { className:'frame-add-box' },
      e('button', { className:'close-x', onClick: onClose,
        style:{ position:'absolute', top:20, right:20, color:'var(--ink-2)', width:32, height:32, display:'grid', placeItems:'center' } }, e(Icon.x)),
      e('div', { style:{ fontFamily:'var(--font-ui)', fontSize:10, letterSpacing:'.25em', textTransform:'uppercase', color:'var(--accent)', marginBottom:10 } },
        initial ? 'Редактирование' : 'Новый кадр'),
      e('h3', null, initial ? 'Редактировать кадр' : 'Добавить кадр'),
      e('div', { className:'sub' }, initial ? 'Поля можно менять — изображение остаётся.' : 'Перетащите изображение или нажмите, чтобы выбрать.'),
      !initial && e('div', { className:'frame-dropzone ' + (drag?'drag-over':'') + (imageData?' has-image':''),
        onClick: () => fileRef.current?.click(),
        onDragOver: ev => { ev.preventDefault(); setDrag(true); },
        onDragLeave: () => setDrag(false),
        onDrop: ev => { ev.preventDefault(); setDrag(false); handleFile(ev.dataTransfer.files[0]); } },
        imageData
          ? e('img', { src: imageData })
          : e('div', null,
              e('div', { className:'icon' }, '⛶'),
              'Перетащите кадр сюда',
              e('br'),
              e('span', { style:{ fontSize:9, color:'var(--ink-3)', fontStyle:'italic', textTransform:'none', letterSpacing:0 } }, 'или нажмите, чтобы выбрать файл')),
        e('input', { type:'file', accept:'image/*', ref: fileRef, style:{display:'none'},
          onChange: ev => handleFile(ev.target.files[0]) })),
      e('div', { className:'field' },
        e('label', null, 'Фильм'),
        e('input', { placeholder:'Например: Зеркало', value: film, onChange: ev => setFilm(ev.target.value) })),
      e('div', { style:{ display:'grid', gridTemplateColumns:'1fr 100px', gap:10 } },
        e('div', { className:'field' },
          e('label', null, 'Режиссёр'),
          e('input', { placeholder:'Андрей Тарковский', value: director, onChange: ev => setDirector(ev.target.value) })),
        e('div', { className:'field' },
          e('label', null, 'Год'),
          e('input', { placeholder:'1975', value: year, onChange: ev => setYear(ev.target.value) }))),
      e('div', { className:'field' },
        e('label', null, 'Почему этот кадр'),
        e('textarea', { placeholder:'Разбор композиции, света, цвета…', value: why, onChange: ev => setWhy(ev.target.value) })),
      e('div', { className:'field' },
        e('label', null, 'Теги'),
        e('input', { placeholder:'длинный план, природный свет…', value: tags, onChange: ev => setTags(ev.target.value) })),
      e('div', { className:'frame-add-actions' },
        e('button', { className:'btn', onClick: onClose }, 'Отмена'),
        e('button', { className:'btn primary', onClick: save, disabled: saving },
          saving ? 'Сохраняю…' : (initial ? 'Сохранить изменения' : 'Сохранить кадр')))));
}

// ═══════════ POEM MODAL ═══════════
function PoemModal({ poem, onClose, onFav, fav }) {
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    if (!poem) { setText(null); setEditing(false); return; }
    if (poem.full && poem.full.length && poem.full.some(l => l.trim())) {
      setText(poem.full.join('\n'));
      return;
    }
    if (!poem.link) {
      setText((poem.excerpt || []).join('\n'));
      return;
    }
    setLoading(true);
    fetch('/api/poem?url=' + encodeURIComponent(poem.link))
      .then(r => r.json())
      .then(d => {
        if (d.ok && d.text) setText(d.text);
        else setText((poem.excerpt || []).join('\n') + '\n\n[полный текст недоступен]');
      })
      .catch(() => setText((poem.excerpt || []).join('\n')))
      .finally(() => setLoading(false));
  }, [poem]);

  if (!poem) return null;

  const startEdit = () => {
    setDraft(text && !/недоступен/.test(text) ? text : '');
    setEditing(true);
  };

  const saveManual = async () => {
    if (!poem.link) { toast('Нет ссылки — некуда привязать'); return; }
    const r = await fetch('/api/manual-text', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ link: poem.link, text: draft })
    });
    const j = await r.json();
    if (j.ok) { toast('Сохранено'); setText(draft); setEditing(false); }
    else toast('Ошибка: ' + (j.error || ''));
  };

  return e('div', { className:'overlay', onClick: ev => { if (ev.target.classList.contains('overlay')) onClose(); } },
    e('div', { className:'poem-modal' },
      e('button', { className:'close-x', onClick: onClose }, e(Icon.x)),
      e('div', { className:'kicker' }, poem.category || 'Стихотворение'),
      e('h2', null, poem.title),
      e('div', { className:'author-line' }, poem.author, poem.year ? ' · ' + poem.year : ''),
      e('div', { className:'poem-text' },
        editing
          ? e('textarea', { value: draft, onChange: ev => setDraft(ev.target.value), autoFocus:true, placeholder:'Вставьте или наберите текст…' })
          : (loading ? '⋯ загружаю текст…' : (text || (poem.excerpt || []).join('\n')))),
      e('div', { className:'modal-actions' },
        !editing && e('button', { className:'btn', onClick: () => copy(text || '') }, e(Icon.copy), 'Скопировать'),
        !editing && e('button', { className: 'btn '+(fav?'primary':''), onClick: onFav }, e(Icon.heart, null, fav), fav?'В избранном':'В избранное'),
        !editing && poem.link && e('button', { className:'btn', onClick: startEdit }, e(Icon.edit), 'Вписать вручную'),
        !editing && poem.link && e('a', { className:'btn', href: poem.link, target:'_blank', rel:'noreferrer' }, e(Icon.open), 'Источник'),
        editing && e('button', { className:'btn primary', onClick: saveManual }, 'Сохранить'),
        editing && e('button', { className:'btn', onClick: () => setEditing(false) }, 'Отмена'),
        !editing && e('button', { className:'btn', onClick: onClose }, 'Закрыть'))));
}

// ═══════════ APP ═══════════
function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('seance.theme') || 'dark');
  const [section, setSection] = useState(() => localStorage.getItem('seance.section') || 'lit');
  const [litSub, setLitSub] = useState(() => localStorage.getItem('seance.litSub') || 'roulette');
  const [cineSub, setCineSub] = useState(() => localStorage.getItem('seance.cineSub') || 'roulette');
  const [favs, setFavs] = useState(() => new Set(JSON.parse(localStorage.getItem('seance.favs') || '[]')));
  const favsSyncedRef = useRef(false);
  const favsSaveTimer = useRef(null);
  const [openedPoem, setOpenedPoem] = useState(null);
  const [data, setData] = useState(null);
  const [prefillAuthor, setPrefillAuthor] = useState(null);

  const [backStack, setBackStack] = useState([]);
  const currentRef = useRef({ section, litSub, cineSub });
  useEffect(() => { currentRef.current = { section, litSub, cineSub }; }, [section, litSub, cineSub]);

  // Пушим в стек не только навигационный state, но и текущую позицию скролла —
  // при «Назад» восстанавливаем её, чтобы возврат в галерею не прыгал в начало.
  const pushBack = () => setBackStack(s => [
    ...s,
    { ...currentRef.current, scrollY: window.scrollY || window.pageYOffset || 0 }
  ].slice(-20));

  const navSection = (v) => {
    if (v === section) return;
    pushBack();
    setSection(v);
  };
  const navLitSub = (v) => {
    if (v === litSub) return;
    pushBack();
    setLitSub(v);
  };
  const navCineSub = (v) => {
    if (v === cineSub) return;
    pushBack();
    setCineSub(v);
  };

  const goBack = () => {
    if (openedPoem) { setOpenedPoem(null); return; }
    if (!backStack.length) return;
    const last = backStack[backStack.length - 1];
    setBackStack(backStack.slice(0, -1));
    setSection(last.section);
    setLitSub(last.litSub);
    setCineSub(last.cineSub);
    // Восстанавливаем прежнюю позицию скролла после того как React отрисует
    // новую секцию. 40 мс хватает для перемонтажа Cinema/Library.
    if (typeof last.scrollY === 'number') {
      setTimeout(() => {
        try { window.scrollTo({ top: last.scrollY, behavior: 'instant' }); }
        catch (e) { window.scrollTo(0, last.scrollY); }
      }, 40);
    }
  };

  useEffect(() => {
    const onKey = (ev) => {
      if (ev.key === 'Escape' && !openedPoem && backStack.length) goBack();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [backStack.length, openedPoem]);

  useEffect(() => { document.body.dataset.theme = theme; localStorage.setItem('seance.theme', theme); }, [theme]);
  useEffect(() => { localStorage.setItem('seance.section', section); }, [section]);
  useEffect(() => { localStorage.setItem('seance.litSub', litSub); }, [litSub]);
  useEffect(() => { localStorage.setItem('seance.cineSub', cineSub); }, [cineSub]);
  useEffect(() => {
    localStorage.setItem('seance.favs', JSON.stringify([...favs]));
    if (!favsSyncedRef.current) return;
    if (favsSaveTimer.current) clearTimeout(favsSaveTimer.current);
    favsSaveTimer.current = setTimeout(() => {
      fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...favs] }),
      }).catch(() => {});
    }, 400);
  }, [favs]);

  useEffect(() => {
    fetch('/api/favorites').then(r => r.json()).then(d => {
      if (d && d.ok && Array.isArray(d.ids)) {
        setFavs(prev => {
          const merged = new Set(prev);
          for (const id of d.ids) merged.add(id);
          if (merged.size !== prev.size) return merged;
          for (const id of d.ids) if (!prev.has(id)) return merged;
          return prev;
        });
      }
    }).catch(() => {}).finally(() => { favsSyncedRef.current = true; });
  }, []);

  const reload = async () => {
    const r = await loadAll();
    setData(buildDataset(r.raw, r.frames));
  };
  useEffect(() => { reload(); }, []);

  // Прогрев кеша картинок: переключение «Следующий →» в кино-рулетке
  // должно быть мгновенным, без мигания. Создаём Image-объекты, браузер
  // подтягивает все 15 кадров в фоне, потом отдаёт из кеша.
  useEffect(() => {
    if (!data || !data.FRAMES) return;
    for (const f of data.FRAMES) {
      if (f.img) { const im = new Image(); im.src = f.img; }
    }
  }, [data]);

  const toggleFav = id => setFavs(prev => {
    const n = new Set(prev);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });

  const goToAuthor = (author) => {
    pushBack();
    setSection('lit');
    setLitSub('library');
    setPrefillAuthor(author);
  };

  if (!data) {
    return e('div', { className:'shell' },
      e(Masthead, { theme, setTheme }),
      e(Tagline),
      e('div', { className:'loading-screen' },
        e('span', { className:'spin' }, '✦'),
        'Собираю сеанс…'));
  }

  const litBody = litSub === 'library'
    ? e(Library, { data, openPoem: setOpenedPoem, favs, toggleFav,
        prefillAuthor, clearPrefill: () => setPrefillAuthor(null) })
    : litSub === 'favorites'
      ? e(Roulette, { data, openPoem: setOpenedPoem, favs, toggleFav,
          onAuthorClick: goToAuthor, lockedCategory: 'fav' })
      : e(Roulette, { data, openPoem: setOpenedPoem, favs, toggleFav, onAuthorClick: goToAuthor });

  const showBack = backStack.length > 0 || !!openedPoem;

  return e('div', { className:'shell' },
    showBack && e('button', { className:'back-btn', onClick: goBack, title: openedPoem ? 'Закрыть' : 'Назад' },
      e(Icon.arrowLeft)),
    e(Masthead, { theme, setTheme }),
    e(Tagline),
    e(Sections, { section, setSection: navSection }),
    section === 'lit'
      ? e('div', { className:'view active' },
          e(SubNav, { items:[
              {id:'roulette',label:'Рулетка'},
              {id:'library',label:'Библиотека поэтов'},
              {id:'favorites',label:'♥ Избранное' + (favs.size ? ' · ' + favs.size : '')}
            ], active: litSub, onChange: navLitSub }),
          litBody)
      : e(Cinema, { data, subSection: cineSub, setSubSection: navCineSub,
          favs, toggleFav, onFrameChanged: reload }),
    e('footer', { className:'footer' },
      e('div', { className:'stat' },
        e('span', null, e('strong', null, (data.POEMS.length + data.QUOTES.length).toLocaleString('ru-RU')), ' отрывков'),
        e('span', null, e('strong', null, data.FRAMES.length), ' кадров'),
        e('span', null, e('strong', null, favs.size), ' в избранном')),
      e('div', null, 'Сеанс · сделано для одного')),
    e(PoemModal, {
      poem: openedPoem,
      onClose: () => setOpenedPoem(null),
      onFav: () => openedPoem && toggleFav(openedPoem.id),
      fav: openedPoem ? favs.has(openedPoem.id) : false,
    }));
}

const rootEl = document.getElementById('root');
rootEl.innerHTML = '';
ReactDOM.createRoot(rootEl).render(e(App));
})();
</script>
</body>
</html>"""























# ---------- ВЕБ-СЕРВЕР ----------

# Кэш сжатых gzip-ответов. Ключ — (len, первые 64 байта, последние 64 байта).
# Этого с запасом хватает: разный контент почти всегда отличается по длине,
# а внутри одного контента 128 байт краёв — уникальный отпечаток.
_GZIP_CACHE = {}
_GZIP_CACHE_LOCK = threading.Lock()
_GZIP_CACHE_MAX = 4  # HTML + /api/poems + пара запасных


def _gzip_cached(body: bytes) -> bytes:
    """gzip(body) с кэшем. Инвалидация автоматическая — ключ зависит от контента."""
    import gzip
    key = (len(body), body[:64], body[-64:])
    with _GZIP_CACHE_LOCK:
        hit = _GZIP_CACHE.get(key)
        if hit is not None:
            return hit
    # Сжимаем вне локи, чтобы параллельные клиенты с другим контентом не ждали.
    compressed = gzip.compress(body, compresslevel=6)
    with _GZIP_CACHE_LOCK:
        if len(_GZIP_CACHE) >= _GZIP_CACHE_MAX:
            _GZIP_CACHE.pop(next(iter(_GZIP_CACHE)))
        _GZIP_CACHE[key] = compressed
    return compressed


class AppHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _write_body(self, body):
        # Защита от OSError [Errno 55] "No buffer space available" на macOS.
        # Проблема — не размер куска, а истощение mbuf (сетевых буферов ядра).
        # Поэтому три слоя: маленький chunk 64КБ, retry с backoff, graceful
        # выход на разрыве клиента.
        chunk = 65536
        i = 0
        n = len(body)
        while i < n:
            piece = body[i:i + chunk]
            for attempt in range(6):
                try:
                    self.wfile.write(piece)
                    self.wfile.flush()
                    break
                except (BrokenPipeError, ConnectionResetError):
                    return  # клиент закрыл — выходим тихо
                except OSError as e:
                    # Errno 55 на macOS / Errno 105 (ENOBUFS) на Linux —
                    # ядро просит подождать пока освободится буфер.
                    if e.errno in (55, 105) and attempt < 5:
                        time.sleep(0.02 * (2 ** attempt))  # 20→40→80→160→320 мс
                        continue
                    return  # иначе — тихо прерываем запрос, но не роняем сервер
            i += chunk

    def _respond_bytes(self, status, body, content_type):
        """Отдаёт байты клиенту. Для body >64КБ жмёт gzip, если клиент принимает.
        Это главный фикс проблемы с OSError [Errno 55] на /api/poems:
        36МБ JSON превращается в ~9МБ, sendall перестаёт захлёбываться.
        Результат gzip кэшируется по содержимому — повторная отдача той же
        базы идёт из памяти без ре-сжатия (~2с → ~0мс)."""
        if len(body) > 65536 and 'gzip' in (self.headers.get('Accept-Encoding') or '').lower():
            body = _gzip_cached(body)
            encoding = 'gzip'
        else:
            encoding = None
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if encoding:
            self.send_header('Content-Encoding', encoding)
        self.end_headers()
        self._write_body(body)

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._respond_bytes(status, body, 'application/json; charset=utf-8')

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ('/', '/index.html'):
            body = HTML_PAGE.encode('utf-8')
            self._respond_bytes(200, body, 'text/html; charset=utf-8')
        elif path == '/api/poems':
            self._json(200, load_data())
        elif path == '/api/update/status':
            self._json(200, progress_get())
        elif path == '/api/poem':
            qs = parse_qs(parsed.query)
            url = (qs.get('url') or [''])[0]
            try:
                data = fetch_poem_text(unquote(url))
                self._json(200, {"ok": True, **data})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
        elif path == '/api/stats':
            data = load_data()
            stats = {
                "data_dir": str(DATA_DIR),
                "data_file": str(DATA_FILE),
                "total": len(data),
                "with_text": sum(1 for it in data if it.get("text")),
                "authors": len({it.get("author") for it in data if it.get("author")}),
                "bytes": DATA_FILE.stat().st_size if DATA_FILE.exists() else 0,
                "corpus": corpus_status(),
            }
            self._json(200, stats)
        elif path == '/api/open-data-folder':
            import subprocess
            try:
                subprocess.Popen(["open", str(DATA_DIR)])
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
        elif path == '/api/screenshots':
            self._json(200, load_screenshots())
        elif path == '/api/favorites':
            self._json(200, {"ok": True, "ids": load_favorites()})
        elif path.startswith('/img/'):
            # Отдача картинок-кадров. /img/<filename>
            fname = unquote(path[len('/img/'):])
            # защита от path traversal
            if '/' in fname or '\\' in fname or '..' in fname:
                self.send_error(404); return
            target = SCREENSHOTS_DIR / fname
            if not target.exists() or not target.is_file():
                self.send_error(404); return
            ext = fname.rsplit('.', 1)[-1].lower()
            mime = {
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'png': 'image/png', 'webp': 'image/webp',
                'gif': 'image/gif', 'heic': 'image/heic',
            }.get(ext, 'application/octet-stream')
            data = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self._write_body(data)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/update':
            with PROGRESS_LOCK:
                if PROGRESS["running"]:
                    self._json(409, {"ok": False, "error": "Парсинг уже идёт"})
                    return
            threading.Thread(target=_run_update, daemon=True).start()
            self._json(202, {"ok": True, "started": True})
        elif path == '/api/download-texts':
            with PROGRESS_LOCK:
                if PROGRESS["running"]:
                    self._json(409, {"ok": False, "error": "Другая операция уже идёт"})
                    return
            threading.Thread(target=_run_download_texts, daemon=True).start()
            self._json(202, {"ok": True, "started": True})
        elif path == '/api/stop':
            # Ставим флаг. Рабочие потоки проверят и выйдут, сохранив состояние.
            CANCEL_EVENT.set()
            self._json(200, {"ok": True, "cancel": True})
        elif path == '/api/manual-text':
            # Ручное сохранение текста стиха. {"link": "...", "text": "..."}
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                ok, err = save_manual_text(body.get('link', ''),
                                           body.get('text', ''))
                self._json(200 if ok else 400,
                           {"ok": ok, "error": err} if not ok else {"ok": True})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
        elif path == '/api/favorites':
            # Сохранение полного списка избранного. {"ids": ["...", "..."]}
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                ids = body.get('ids', [])
                if not isinstance(ids, list):
                    self._json(400, {"ok": False, "error": "ids должно быть list"})
                    return
                saved = save_favorites(ids)
                self._json(200, {"ok": True, "ids": saved})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
        elif path == '/api/screenshots':
            # Создание нового кадра. {"image_base64", "original_filename",
            # "film", "director", "year", "why", "tags": [...]}
            try:
                import base64
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                img_b64 = body.get('image_base64', '')
                # допустим формат "data:image/jpeg;base64,XXXX" или просто base64
                if img_b64.startswith('data:'):
                    img_b64 = img_b64.split(',', 1)[1]
                img_bytes = base64.b64decode(img_b64)
                if len(img_bytes) > 20 * 1024 * 1024:
                    self._json(400, {"ok": False, "error": "файл больше 20 МБ"})
                    return
                rec = add_screenshot(body, img_bytes,
                                     body.get('original_filename', 'image.jpg'))
                self._json(200, {"ok": True, "screenshot": rec})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
        elif path.startswith('/api/screenshots/'):
            # PATCH /api/screenshots/<id> — изменить метаданные
            sid = path[len('/api/screenshots/'):]
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                rec = update_screenshot(sid, body)
                if rec:
                    self._json(200, {"ok": True, "screenshot": rec})
                else:
                    self._json(404, {"ok": False, "error": "не найден"})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})
        else:
            self.send_error(404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith('/api/screenshots/'):
            sid = path[len('/api/screenshots/'):]
            ok = delete_screenshot(sid)
            self._json(200 if ok else 404, {"ok": ok})
        else:
            self.send_error(404)


MANUAL_CSV = DATA_DIR / "corpus_manual.csv"
_MANUAL_LOCK = threading.Lock()


def save_manual_text(link, text):
    """Сохраняет вручную введённый текст стиха.

    1) Обновляет item['text'] в poems.json (для немедленного эффекта).
    2) Дописывает в corpus_manual.csv (для бэкапа и переиспользования через
       _try_corpus, если кто-то когда-то перезальёт базу).

    Возвращает (ok, error).
    """
    if not link or not isinstance(link, str):
        return False, "пустая ссылка"
    text = (text or '').strip()
    if len(text) < 5:
        return False, "слишком короткий текст"
    text = text[:20000]

    with _MANUAL_LOCK:
        # 1) Обновляем основную базу
        data = load_data()
        target = next((it for it in data if it.get('link') == link), None)
        if not target:
            return False, "стих не найден в базе"
        target['text'] = text
        save_data(data)

        # 2) Дописываем в corpus_manual.csv (заменяем если уже есть)
        import csv
        rows = []
        if MANUAL_CSV.exists():
            with open(MANUAL_CSV, 'r', encoding='utf-8') as f:
                rows = [r for r in csv.DictReader(f)
                        if r.get('link') != link]
        rows.append({
            'author': target.get('author', ''),
            'title': target.get('title', ''),
            'link': link,
            'text': text,
        })
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(MANUAL_CSV, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['author', 'title', 'link', 'text'])
            w.writeheader()
            w.writerows(rows)

        # 3) Инвалидируем кэш стиха (если он был закэширован для on-demand)
        with _POEM_CACHE_LOCK:
            _POEM_CACHE.pop(link, None)

    return True, None


def _run_download_texts():
    try:
        download_missing_texts()
    except Exception as e:
        progress_set(running=False, ok=False, error=str(e), stage="Ошибка")


def _run_update():
    try:
        data = scrape_all()
        # Защита: если новый результат пуст или сильно меньше существующего —
        # значит парсер столкнулся с сетевой проблемой. Не затираем рабочий кеш.
        existing = load_data()
        if not data:
            progress_set(running=False, ok=False,
                         error="Парсер ничего не нашёл (возможно, сайт недоступен). Старый кеш не тронут.",
                         stage="Ошибка")
            return
        if existing and len(data) < len(existing) * 0.5:
            progress_set(running=False, ok=False,
                         error=f"Новый результат ({len(data)}) сильно меньше старого ({len(existing)}). Кеш не перезаписан — попробуй позже.",
                         stage="Ошибка")
            return
        save_data(data)
        progress_set(running=False, ok=True, error=None, stage="Готово",
                     items=len(data))
    except InterruptedError:
        # Пользователь нажал «Остановить» — не ошибка. Уже сохранено внутри.
        current = load_data()
        with_text = sum(1 for it in current if it.get("text"))
        progress_set(running=False, ok=True,
                     stage=f"Остановлено. В базе {len(current)} записей "
                           f"({with_text} с текстом). Нажми «📥 Скачать тексты», "
                           f"чтобы докачать оставшееся.",
                     items=len(current))
    except Exception as e:
        progress_set(running=False, ok=False, error=str(e), stage="Ошибка")


# ---------- ЗАПУСК ----------
def main():
    print("=" * 50)
    print("  📽  Сеанс — литературно-визуальная рулетка")
    print("=" * 50)

    try:
        import requests  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        print("\n⚠️  Нужны библиотеки: requests, beautifulsoup4, lxml")
        print("   pip3 install requests beautifulsoup4 lxml")
        return

    if not DATA_FILE.exists():
        print("\n🆕 Первый запуск — кэша нет. Открой UI и нажми «Обновить с сайта».")
    else:
        data = load_data()
        print(f"\n📂 Загружено {len(data)} записей из кэша")

    # ThreadingHTTPServer — чтобы один тяжёлый запрос (/api/poems ~36МБ)
    # не блокировал параллельные (картинки, /api/stats, прогресс).
    # SO_SNDBUF=4МБ — чтобы sendall не захлёбывался на больших ответах.
    import socket as _socket
    class _Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        def server_bind(self):
            try:
                self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF, 4 * 1024 * 1024)
            except OSError:
                pass
            super().server_bind()
    server = _Server(('127.0.0.1', PORT), AppHandler)
    print(f"\n🌐 Сервер: http://localhost:{PORT}")

    use_gui = os.environ.get("ASKBUKA_GUI", "1") != "0"
    if use_gui:
        try:
            import webview
        except ImportError:
            use_gui = False
            print("⚠️  pywebview не установлен — открою в браузере")

    if use_gui:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        # Трюк: подменяем CFBundleName в runtime-словаре NSBundle, чтобы в
        # меню-баре macOS писалось «Сеанс», а не «Python». Без этого пункт
        # меню берёт имя от процесса python3 (бандла Python.framework).
        try:
            from Foundation import NSBundle
            _b = NSBundle.mainBundle()
            _info = _b.localizedInfoDictionary() or _b.infoDictionary()
            if _info is not None:
                _info['CFBundleName'] = 'Сеанс'
                _info['CFBundleDisplayName'] = 'Сеанс'
        except Exception:
            pass
        # Окно по всей доступной высоте visibleFrame (от челки / меню-бара
        # до Dock'а). Ширина остаётся 1100; если экран уже — берём экран.
        win_w, win_h = 1100, 840
        win_x, win_y = None, None
        try:
            from AppKit import NSScreen
            vf = NSScreen.mainScreen().visibleFrame()
            sw, sh = int(vf.size.width), int(vf.size.height)
            win_w = min(win_w, sw)
            win_h = sh
            win_x = max(0, (sw - win_w) // 2)
            win_y = 0  # прямо под меню-бар (visibleFrame это учитывает)
        except Exception:
            pass  # pywebview сам отцентрирует
        try:
            win_kwargs = dict(width=win_w, height=win_h, min_size=(720, 570))
            if win_x is not None and win_y is not None:
                win_kwargs.update(x=win_x, y=win_y)
            webview.create_window(
                "Сеанс",
                f"http://localhost:{PORT}",
                **win_kwargs,
            )
            webview.start()
        finally:
            server.shutdown()
            print("\n👋 До встречи!")
    else:
        print("   (Ctrl+C — остановить)\n")
        threading.Timer(1.0, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 До встречи!")
            server.shutdown()


if __name__ == '__main__':
    main()
