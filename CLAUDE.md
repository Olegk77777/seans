# Сеанс — контекст проекта

Локальное macOS-приложение: «литературно-визуальная рулетка». Два раздела —
**Литература** (стихи + цитаты) и **Кино** (стоп-кадры из фильмов). БАЗА
АВТОНОМНА: многоуровневый fallback — локальные корпуса → Wikisource →
askbooka.ru → Wayback Machine. Изначально парсил askbooka.ru, но имя
проекта обновлено до «Сеанс», бандл — `Сеанс.app`.

## Структура

```
AskBukaPoetry/                           # имя папки проекта оставлено (исторически)
├── app.py                               # ядро: парсер + HTTP + HTML UI (React UMD) + pywebview
├── pdf_to_csv.py                        # парсер PDF-сборников → CSV
├── wikiquote_fetch.py                   # скачивание цитат с ru.wikiquote.org
├── wayback_drain.py                     # массовое скачивание стихов через web.archive.org
├── README.md
├── data/                                # старое расположение кэша (fallback)
├── .claude/launch.json                  # конфиг preview_start
└── Сеанс.app/                           # macOS-бандл
    └── Contents/
        ├── Info.plist                   # CFBundleName=Сеанс
        ├── MacOS/Seans                  # bash-лончер
        └── Resources/
            ├── app.py                   # КОПИЯ app.py (синхронизировать после правок)
            └── AppIcon.icns
```

## Запуск

1. `Сеанс.app/Contents/MacOS/Seans` — bash-лончер. Создаёт venv в Application Support,
   ставит зависимости.
2. **Миграция**: при первом запуске копирует данные из старого
   `~/Library/Application Support/АскБука Поэзии/data/` (если есть) и из
   `data/` рядом с .app, в новую папку.
3. Экспортирует `SEANS_DATA_DIR` → `~/Library/Application Support/Сеанс/data`.
   `app.py` читает обе переменные (`SEANS_DATA_DIR` приоритетнее, fallback —
   `ASKBUKA_DATA_DIR`).
4. Запускает `python3 app.py` → HTTP на `127.0.0.1:8765` (`PORT`) → pywebview
   (фолбэк в браузер, если pywebview недоступен).

### Окно (pywebview)

- Ширина **1100**, высота **= visibleFrame().size.height** (от меню-бара до
  Dock — full vertical). На меньших экранах автоматически ужимается.
- Координаты вычисляются через `AppKit.NSScreen.mainScreen().visibleFrame()`,
  горизонтально по центру, `y=0` (под меню-баром).
- min_size = `(720, 570)`.
- **Имя в меню-баре** (рядом с 🍎) — хак через `NSBundle.mainBundle().
  infoDictionary()` с подменой `CFBundleName='Сеанс'`. Без этого macOS берёт
  имя из `Python.framework/Info.plist` и пишет «Python». `CFBundleName` в
  `Сеанс.app/Contents/Info.plist` игнорируется, т.к. процесс-то запущен из
  `venv/bin/python3`, не из executable самого бандла.

## Зависимости

Внутри venv:
- `requests`, `beautifulsoup4`, `lxml` — парсинг сайтов
- `pywebview[cocoa]`, `pyobjc-*` — нативное окно

Фронтенд — **React 18 UMD с unpkg** (подключается из HTML_PAGE), ничего
собирать не нужно. Для офлайна потребуется интернет при первом запуске
(шрифты Google Fonts + React) — дальше браузер кэширует.

Дополнительно (для скриптов парсинга PDF/цитат, не нужны для рантайма приложения):
- `pymupdf` (`fitz`) — извлечение текста из PDF (используется в `pdf_to_csv.py`)
- стандартные библиотеки Python — для `wikiquote_fetch.py` и `wayback_drain.py`

Homebrew Python (PEP 668) → **только venv**, никакого `--user`.

## База данных и источники текстов

Архитектура источников (по убыванию приоритета — от мгновенного к медленному):

1. **Локальный корпус** (`corpus_*.csv` в DATA_DIR) — мгновенно, оффлайн.
   - `corpus.csv` — Georgii/russianPoetry с HuggingFace (~16700 стихов классиков, 24 МБ)
   - `corpus_brodsky.csv` — Бродский (vicemik/brodsky-poetry, ~600 стихов)
   - `corpus_ryzhy.csv`, `corpus_ryzhy_2001.csv`, `corpus_ryzhy_2013.csv` —
     спарсенные PDF Бориса Рыжего с imwerden.de
   - `corpus_wayback.csv` — добытое через Wayback (генерируется `wayback_drain.py`)
   - `corpus_manual.csv` — ручные правки текста (через UI-кнопку «✏️ Исправить»)
   - **Любой** `corpus_*.csv` с колонками `author,title,text` подхватывается
     автоматически (см. `_discover_extra_csvs` в [app.py](app.py))
2. **Wikisource API** (ru.wikisource.org) — для классиков, не попавших в корпус (PD).
3. **askbooka.ru** — прямой запрос (бесполезен в Украине, заблокирован).
4. **Wayback Machine** (web.archive.org) — последний рубеж, медленно но почти всегда работает.

**Корпус как первоклассный источник** (с апреля 2026): `load_data()` не только
подставляет тексты в записи `poems.json` через `_try_corpus`, но и **добавляет
корпусные стихи в итоговый список** как отдельные `type=poem` записи (функции
`_parse_corpus_items` + `_load_corpus_as_items`, кэш `_CORPUS_ITEMS_CACHE`).
Дедупликация по `(author, _norm_text(title))` с уже присутствующими в
`poems.json`. Это дало прирост с ~1600 до **~18 000 стихов** в библиотеке.
Payload `/api/poems` вырос до ~36 МБ, для локалхоста терпимо.

### Цитаты

`quotes_*.csv` в DATA_DIR — формат `author,quote,source`. Подмешиваются в базу
функцией `_load_quote_csvs()` как записи `type=quote`, `category=Цитаты`.
Источник — Викицитатник (ru.wikiquote.org). Скачивает `wikiquote_fetch.py`.

## Пути и окружение

- Данные: `~/Library/Application Support/Сеанс/data/`
  - `poems.json` — основная база (стихи + полные тексты)
  - `corpus_*.csv` — корпуса с текстами стихов
  - `quotes_*.csv` — цитаты с Викицитатника
  - `corpus_manual.csv` — ручные правки
  - `screenshots.json` + `screenshots/` — кадры из кино и метаданные
  - `favorites.json` — серверное хранение избранного (видно в подтабе ♥)
- venv: `~/Library/Application Support/Сеанс/venv/`
- Лог: `~/Library/Application Support/Сеанс/app.log`
- PID: `~/Library/Application Support/Сеанс/app.pid`
- Override: `SEANS_DATA_DIR=...` (приоритетно) или `ASKBUKA_DATA_DIR=...`
- Фолбэк в браузер: `ASKBUKA_GUI=0 python3 app.py`

## Скрипты вне рантайма

### `pdf_to_csv.py` — парсер PDF-сборников

Заточен под формат imwerden.de. Поддерживает 3 разновидности разметки:
заголовки заглавными + дата, разделители-звёздочки `*** ❖ ^ <►`, разделители
номерами страниц.

```bash
python3 pdf_to_csv.py "<URL-или-путь>" "Имя Автора" \
    "/path/to/data/corpus_<имя>.csv"
```

### `wikiquote_fetch.py` — скачивание цитат

Парсит `{{Q|...}}` и `{{Цитата|...|Автор=...}}`, чистит вики-разметку,
отбрасывает секции «Цитаты о …», «Литература», «Ссылки» и т.п.

```bash
python3 wikiquote_fetch.py "Виктор Цой" "Курт Кобейн" "Уинстон Черчилль" ...
```

Каждое имя → отдельный `quotes_<slug>.csv`.

### `wayback_drain.py` — массовая дозагрузка через архив

Идёт по всем записям без `text` в `poems.json`, скачивает страницы askbooka
из Wayback Machine. С задержкой 2.5 сек между запросами (rate limit). Пишет
инкрементально каждые 30 сек в `corpus_wayback.csv` + `poems.json`. При
сбое можно перезапустить — пропустит уже скачанное.

Лог: `/tmp/wayback_drain.log`. Темп: ~6 успехов/мин (зависит от Wayback).

## Частые операции

- **Порт занят:** `lsof -iTCP:8765 -sTCP:LISTEN -n -P`
- **Убить процесс:** `kill $(cat "~/Library/Application Support/Сеанс/app.pid")`
- **Пересобрать иконку:**
  ```
  SRC=path/to/icon.png
  ICONSET=/tmp/AppIcon.iconset; rm -rf "$ICONSET" && mkdir -p "$ICONSET"
  for s in 16 32 64 128 256 512 1024; do sips -z $s $s "$SRC" --out "$ICONSET/icon_${s}x${s}.png"; done
  iconutil -c icns "$ICONSET" -o "Сеанс.app/Contents/Resources/AppIcon.icns"
  ```
- **Сброс кэша иконок Finder:** `touch .app && lsregister -f .app && killall Dock Finder`
- **Синхронизация с бандлом** (после правок `app.py` в корне):
  ```
  cp app.py "Сеанс.app/Contents/Resources/app.py"
  ```
- **Бэкап базы:** просто скопировать папку
  `~/Library/Application Support/Сеанс/data/` куда угодно.

## UI (редизайн по Seance-handoff, апрель 2026)

Журнальная вёрстка, тёмная тема по умолчанию, плёночное SVG-зерно всегда
включено. Фронтенд — **React 18 UMD**, весь JSX в `HTML_PAGE` внутри
[app.py](app.py) (строки ~1469–3070).

### Типографика

- `--font-display`: DM Serif Display — заголовки, буквицы
- `--font-body`: EB Garamond — текст стихов/цитат (италик на кавычках)
- `--font-ui`: Space Grotesk — UI-подписи uppercase с letter-spacing
- `--font-mono`: JetBrains Mono — номера страниц, год

Подключаются через Google Fonts с preconnect.

### Палитра

Тёмная (default):
- `--ink` #EDE4D3 — основной текст
- `--paper` #1A1714 — фон
- `--accent` #B88A60 — золото-янтарь (em-акцент в h1, буквицы, активные элементы)
- `--alert` #A03A2E — кирпичный для избранного
- `--stamp` #8A5A44 — штамп на цитатах

Светлая (по переключателю): бежевая «бумажная» с тёмно-шоколадным
текстом. Обе темы описаны в одном CSS, переключаются `body[data-theme='light']`.

### Навигация

- **Masthead**: «№ 01 · месяц» · «Сеанс» (центр) · переключатель темы (☾/☀).
- **Секции** (tabs): `— I — Литература` / `— II — Кино`.
- **Subnav** — три подтаба в каждом разделе:
  - Литература: **Рулетка** / **Библиотека поэтов** / **♥ Избранное · N**
  - Кино: **Рулетка** / **Галерея** / **♥ Избранное · N**
- **Back-стрелка** (плавающая слева, только когда есть куда вернуться) —
  стек навигации на основе `pushBack()` + обёрток
  `navSection/navLitSub/navCineSub` + `goToAuthor`. Также закрывает
  открытую модалку стиха. Escape — тоже back.

### Рулетка (Литература)

- Показывает **5 карточек** подряд (`CARDS_PER_PAGE=5`).
- Колода тасуется через Fisher-Yates (`shuffleArray()` в app.py).
  **ВАЖНО**: порядок фильтраций — `deckBase → deckWithQuery →
  shuffledBase → shuffled`. Favs фильтруются ПОСЛЕ тасовки, иначе
  при клике на ♥ вся колода перетасовывалась и карточка «улетала».
- «Перемешать» меняет `shuffleToken` → новая перестановка.
- Next/Prev шагают по 5 в пределах текущей перестановки.
- Категории скрыты в раскрывающемся `<details class="filter-drawer">`
  с `max-height: 260px` и скроллом (~60+ категорий, заголовок
  «Категория · Всё ▾»).
- Поиск фильтрует автора + название + текст (normalize ё→е, убрать пунктуацию).
- В режиме «Избранное» drawer скрыт (lockedCategory='fav'), hero меняет
  текст на «Избранные строки · N сохранено».

### Карточка стиха (PoemCard)

- Колофон сверху: `стр. 001 · ✦ Категория · Отрывок`.
- **Буквица (drop cap)** — inline, 34px, акцентный янтарный. Правила:
  - Не рисуется если в отрывке одна строка (класс `.excerpt.no-drop` +
    уменьшенный padding-left).
  - Не рисуется если первый символ не буква (`\p{L}` regex) — например,
    «…но вот зима» начинается с «…», и буквица пропускается.
- Мета: автор (dashed underline, клик → библиотека с фильтром),
  название в кавычках, год в mono.
- Tools: ♥ (избранное), ⧉ (копировать отрывок), ↗ (открыть модалку).

### Карточка цитаты (QuoteCard)

- Зубчатая рамка-марка (`::before` с dashed border).
- Большая кавычка «слева вверху» (font-display 62px, opacity .35).
- Штамп «СЕАНС · АВТОР» (круглый, accent border, rotate -12°).
- Текст с `padding-right: 84px` чтобы не наезжал на штамп.
- Внизу: имя UPPERCASE + роль курсивом, справа ♥ + ⧉.

### Модалка стиха (PoemModal)

Открывается по клику на ↗ в карточке или из библиотеки. Текст догружается
через `/api/poem?url=...` если полного нет. Кнопки:
- ⧉ Копировать, ♥ В избранное
- ✏️ **Вписать вручную** (только если есть `link`) — открывает textarea,
  сохраняет через `POST /api/manual-text` в `corpus_manual.csv` +
  `poems.json`
- ↗ Источник, Закрыть

### Библиотека поэтов (Library)

- Алфавит кнопками (А-Я) — активные кликабельны, пустые буквы disabled.
- Поиск по фамилии/названию/строке.
- Группировка по буквам: крупная буква 96px + счётчик «N поэт/поэта/поэтов».
- Каждый поэт — раскрывающийся блок (chevron → 90°), внутри — нумерованные
  стихи с excerpt-превью.
- Клик на имени автора из рулетки → автоматический переход сюда с
  prefillAuthor + scrollIntoView.

### Кино (Cinema)

- **Рулетка**: крупный кадр на тёмной карточке, по бокам перфорация
  (dots через `radial-gradient`), «slate» слева сверху с REEL-номером и
  годом/DP. Детали: h2 фильм + курсив режиссёр + mono год, параграф
  «почему», теги-пилюли, кнопки ♥/✏️/⧉/🗑.
- **Галерея**: сетка 16:9 с hover-caption. Поиск по фильму/режиссёру/тегам.
- **Избранное** — как галерея, но с фильтром `favs.has(f.id)`.
- **Zoom overlay**: клик на кадр → fullscreen с strip снизу (title,
  credits DP, why).
- **Добавить/редактировать кадр** (AddFrameOverlay): drag-and-drop
  изображения до 20 МБ, поля film/director/year/why/tags.

## Кадры из кино

Хранение:
- `data/screenshots/<file>` — сами картинки (jpg/png/webp/heic, до 20 МБ)
- `data/screenshots.json` — метаданные: `{id, file, film, director, year, why, tags, note, added}`

### Правило размера (ОБЯЗАТЕЛЬНО)

Любое изображение, которое попадает в `data/screenshots/`, должно быть
уменьшено до **1920 px по большей стороне**. Обоснование:
- Окно приложения 1100×~1000 px, на retina ×2 = ~2200×2000 физически.
- Кадр в UI-рулетке занимает ~900 px логических = ~1800 px на retina.
- 1920 перекрывает retina с запасом, бóльшие размеры — переплата в весе
  без визуальной пользы и риск OSError на отдаче `/img/`.
- Картинки **меньше 1920 по большей стороне не увеличиваем** — Lanczos
  upscale режет резкость. Оставляем как есть.
- Формат: JPEG quality 90 (фото кадров) или WebP quality 85 (компактнее).
  HEIC конвертировать в JPEG.

Способы применить:

1. **Через `sips` (macOS builtin, поддерживает HEIC):**
   ```bash
   sips -Z 1920 input.heic --out output.jpg -s format jpeg
   ```

2. **Через Python/Pillow** (для batch-скриптов):
   ```python
   from PIL import Image
   im = Image.open(src)
   if max(im.size) > 1920:
       im.thumbnail((1920, 1920), Image.LANCZOS)
   im.convert("RGB").save(dst, "JPEG", quality=90, optimize=True)
   ```
   Для HEIC: `pip install pillow pillow-heif` + `register_heif_opener()`.

3. **TODO (не реализовано):** встроить автоматический ресайз прямо в
   `POST /api/screenshots` — сейчас endpoint принимает base64 и пишет
   байты как есть, без преобразования. Когда сделаешь — добавь Pillow
   в venv-зависимости лончера `Сеанс.app/Contents/MacOS/Seans`.

## HTTP API

Читать — GET:
- `GET /` — главная HTML-страница
- `GET /api/poems` — вся база (стихи + цитаты в одном списке)
- `GET /api/poem?url=...` — полный текст стиха (с кэшом)
- `GET /api/stats` — статистика (total/with_text/authors/bytes/corpus)
- `GET /api/screenshots` — список кадров
- `GET /api/favorites` → `{ok, ids: [...]}` — серверный список избранного
- `GET /api/update/status` — прогресс текущей фоновой операции
- `GET /api/open-data-folder` — открыть папку данных в Finder
- `GET /img/<filename>` — отдача картинки кадра

Писать — POST:
- `POST /api/update` — запустить парсинг с сайта (фоновой поток)
- `POST /api/download-texts` — скачать недостающие тексты (wayback/wikisource)
- `POST /api/stop` — остановить любую фоновую операцию (ставит `CANCEL_EVENT`)
- `POST /api/manual-text` — `{link, text}` сохранить вручную вписанный стих
- `POST /api/screenshots` — добавить кадр (`{image_base64, original_filename, film, ...}`)
- `POST /api/screenshots/<id>` — обновить метаданные кадра (без файла)
- `POST /api/favorites` — `{ids: [...]}` → пишет весь список в `favorites.json`

Удалять — DELETE:
- `DELETE /api/screenshots/<id>` — удалить кадр (с файлом)

## Избранное (persistence)

Два слоя:
1. **localStorage** (`seance.favs`) — мгновенный кэш, не требует сети.
2. **`favorites.json`** в DATA_DIR — серверный, **переживает пересборку
   бандла** т.к. лежит в `~/Library/Application Support/Сеанс/data/`,
   а не внутри `.app`.

Поток:
- Init: `GET /api/favorites` → merge с localStorage (ничего не теряется).
- `favsSyncedRef` блокирует сохранение до первого GET, чтобы не затереть
  серверный список пустым.
- Toggle: `setFavs()` → немедленно в localStorage + debounced
  `POST /api/favorites` через 400мс (чтобы не спамить при быстрых кликах).

## Frontend-архитектура

- Всё в `HTML_PAGE = r"""..."""` в [app.py](app.py). Чтобы править
  крупные блоки, удобно держать отдельный файл с тем же raw-string и
  заменять через Python-скрипт-regex (см. историю сессии).
- Компоненты: `App` (корень, стейт) → `Masthead`, `Sections`, `SubNav`,
  `Roulette` / `Library` / `Cinema`, модалки: `PoemModal`,
  `AddFrameOverlay`.
- Навигационный стек (`backStack`) управляется ЯВНЫМ push перед каждым
  переходом через обёртки `navSection/navLitSub/navCineSub`,
  `goToAuthor`. Автоматического push через `useEffect` нет — он
  ненадёжен в pywebview WebKit (не все state-изменения триггерили
  ожидаемую последовательность).
- `pushBack()` сохраняет не только навигационный state, но и `window.scrollY`.
  `goBack()` восстанавливает позицию скролла через `setTimeout 40мс` (ждём
  перемонтажа секции).
- Shuffle: стабильный порядок внутри сессии — `useMemo(shuffleArray(...),
  [deckWithQuery, shuffleToken])`. Favs-фильтр ПОСЛЕ тасовки.
- **Shuffle галереи кинокадров**: токен обновляется **только на явном
  клике подтаба «Галерея»** (через обёртку `onSubNavChange`), а не в
  `useEffect([subSection])`. Это даёт: возврат через «Назад» сохраняет
  порядок, а новый заход из подтаба — перемешивает.

## Поиск — производительность

Поиск по 18к стихам работал тяжело (regex `normalize()` на каждую букву ×
всех стихов). Решения, критичные для UX:

- **Pre-computed haystack**: у каждого POEM/QUOTE/POET есть поле `hay`
  (или `nameHay`) — заранее нормализованная строка `автор + название +
  полный текст`. Фильтр — дешёвый `hay.includes(q)` без regex в цикле.
  Поле заполняется в `buildDataset()` при загрузке датасета.
- **Debounce 250 мс** на вводе в Library/Roulette — через `debouncedQuery`
  state и `useEffect(() => setTimeout(...), [query])`.
- **Лимит на результатах** в Library:
  - `POEMS_PER_BLOCK = 20` — первые 20 стихов в блоке поэта,
    остальное за кнопкой «Показать ещё N» (state `expandedPoets`).
  - `POETS_INITIAL_PAGE = 40` — первые 40 блоков-поэтов при поиске,
    остальное за кнопкой «Показать ещё 40 поэтов» (state `shownPoetsLimit`).
  - Оба сбрасываются в `useEffect([debouncedQuery, activeLetter])`.

Без этих оптимизаций поиск «ночь» (~200 совпадений, тысячи стихов) тормозит
намертво из-за рендера 5000+ DOM-узлов.

## Известные грабли

- **Порт 8765 остаётся занят** после краха — `lsof` перед запуском.
- **preview_start** (Claude Code) идёт мимо venv — pywebview недоступен,
  работает фолбэк в браузер. Это нормально.
- **pywebview требует главный поток** — сервер обязан быть в daemon-thread.
- **Бандл-копия app.py устаревает** — после правок копируй в бандл.
- **Wayback rate limit** — `wayback_drain.py` использует 2.5 сек задержку;
  меньше — словишь `Connection refused`.
- **Wikimedia API требует осмысленный User-Agent** — у нас задан
  `WIKIMEDIA_HEADERS` в [app.py](app.py), не путать с обычным `HEADERS`.
- **React UMD в dev-mode** — `.click()` из eval не всегда триггерит
  React SyntheticEvent; реальные клики пользователя работают нормально.
  Для программной проверки логики кнопок в preview используй
  `el[reactPropsKey].onClick({stopPropagation(){}, preventDefault(){}})`.
- **Fisher-Yates до фильтра избранного** — если наоборот, клик на ♥
  вызовет перетасовку всей колоды и «исчезновение» карточки. Проверено.
- **Первый символ стиха — не буква** (например, «…»): буквицу не рисуем,
  иначе крупная точка выглядит как дефект.

## Планы (что хочется сделать дальше)

### Сделано в сессии апреля 2026

- [x] Полный редизайн UI (Seance-handoff) — journal feel, тёмная/светлая темы
- [x] React 18 UMD в HTML_PAGE (вместо vanilla JS)
- [x] Стопка из 5 карточек в рулетке + Fisher-Yates shuffle
- [x] Категории-фильтры в раскрывающийся drawer
- [x] Back-стрелка с глобальной историей навигации
- [x] Раздел «Избранное» как подтаб + серверное хранение (`favorites.json`)
- [x] Убран дубль кнопки «Галерея» в кино-рулетке
- [x] Buksvitsa (drop cap) inline с проверкой на букву
- [x] Счётчик избранного в подтабах

### Ближайшее

- **Расширение коллекции кадров** — добавить ещё больше через UI-форму
  (drag-and-drop работает). Возможно, доп. поле «оператор» (DOP) и timecode.
- **Категории цитат** — разделить на «Цитаты музыкантов»,
  «Цитаты политиков», «Цитаты режиссёров», «Цитаты философов».
- **Поиск среди кадров по фразе из «почему»** — сейчас только по фильму/режиссёру/тегам.
- **Impression сессии** — показывать при старте «Сегодня был прочитан
  стих Х автором Y» типа дневника.

### Long-term

- **Ad-hoc подпись бандла:** `codesign --force --deep --sign - "Сеанс.app"`.
- **Pre-warm venv** при сборке бандла — чтобы первый запуск не ждал `pip install`.
- **Один источник правды для `app.py`** — сейчас две копии (корень + бандл),
  можно заменить одну на симлинк (но ломает распространяемость бандла).
- **Импорт PDF прямо из UI** — кнопка «📚 Добавить книгу из PDF» с textarea
  для URL. Требует добавить `pymupdf` в venv-зависимости.
- **Автообновление цитат** — кнопка «🔄 Обновить цитаты» в UI, которая
  перезапускает `wikiquote_fetch.py` для всех уже скачанных людей.
- **Вынести HTML_PAGE в отдельный файл** `ui.html` + сборка в app.py при
  запуске — чтобы правки фронта не конкурировали с backend-логикой в
  одном файле на 3000+ строк.
