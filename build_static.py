#!/usr/bin/env python3
"""
Сборщик статической версии «Сеанса» для GitHub Pages.

Что делает:
  1. Извлекает HTML_PAGE из app.py.
  2. Применяет серию правок: эндпоинты → JSON-файлы, кнопки записи спрятаны,
     избранное вырезано (статика — режим «только смотреть»).
  3. Кладёт результат в docs/index.html.

Что НЕ делает:
  - Не снимает свежий snapshot данных. Для этого:
        запусти приложение → curl http://127.0.0.1:8765/api/poems > docs/data.json
        curl http://127.0.0.1:8765/api/screenshots > docs/screenshots.json
        cp -R "$DATA_DIR/screenshots" docs/screenshots
    Или используй флаг --snapshot (см. ниже).

Использование:
    python3 build_static.py             # пересобрать только index.html
    python3 build_static.py --snapshot  # дополнительно — свежий snapshot
                                        # (требует запущенный app.py на :8765)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_PY = ROOT / "app.py"
OUT_DIR = ROOT / "docs"

DATA_DIR_ENV = os.environ.get("SEANS_DATA_DIR") or os.environ.get("ASKBUKA_DATA_DIR")
DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "Сеанс" / "data"
DATA_DIR = Path(DATA_DIR_ENV) if DATA_DIR_ENV else DEFAULT_DATA_DIR

SERVER_URL = "http://127.0.0.1:8765"

# Чтобы импорт app.py видел тот же корпус, что и наш билд.
os.environ.setdefault("SEANS_DATA_DIR", str(DATA_DIR))


# ─────────────────────────────────────────────────────────────────
# 1. Извлечение HTML_PAGE из app.py
# ─────────────────────────────────────────────────────────────────

def extract_html_page(src: str) -> str:
    m = re.search(r'^HTML_PAGE\s*=\s*r"""(.*?)"""\s*$', src, re.DOTALL | re.MULTILINE)
    if not m:
        raise SystemExit("Не нашёл HTML_PAGE в app.py")
    return m.group(1)


# ─────────────────────────────────────────────────────────────────
# 2. Точечные правки HTML_PAGE для статической версии
# ─────────────────────────────────────────────────────────────────

EDITS: list[tuple[str, str, str, int]] = [
    # ── ENDPOINTS DATA ────────────────────────────────────────────
    (
        "endpoint /api/poems → ./data.json",
        "fetch('/api/poems').then(r => r.json()).catch(() => [])",
        "fetch('./data.json').then(r => r.json()).catch(() => [])",
        1,
    ),
    (
        "endpoint /api/screenshots → ./screenshots.json",
        "fetch('/api/screenshots').then(r => r.json()).catch(() => [])",
        "fetch('./screenshots.json').then(r => r.json()).catch(() => [])",
        1,
    ),
    (
        "img path /img/<file> → ./screenshots/<file>",
        "img: f.file ? ('/img/' + encodeURIComponent(f.file)) : null,",
        "img: f.file ? ('./screenshots/' + encodeURIComponent(f.file)) : null,",
        1,
    ),

    # ── ИЗБРАННОЕ: серверные fetch'и → no-op ─────────────────────
    (
        "GET /api/favorites → пустой массив (статика, без сервера)",
        "fetch('/api/favorites').then(r => r.json()).then(d => {",
        "Promise.resolve({ok:true, ids:[]}).then(d => {",
        1,
    ),
    (
        "POST /api/favorites → пустой no-op (статика)",
        """      fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...favs] }),
      }).catch(() => {});""",
        "      /* STATIC: favs only in localStorage, nothing to send */",
        1,
    ),

    # ── /api/poem?url= → пустой ответ (тексты уже все в data.json) ─
    (
        "GET /api/poem → возвращаем пустой результат (тексты уже в data.json)",
        "fetch('/api/poem?url=' + encodeURIComponent(poem.link))\n      .then(r => r.json())",
        "Promise.resolve({ ok:false }).then(r => r)",
        1,
    ),

    # ── СПРЯТАТЬ ♥-КНОПКИ НА КАРТОЧКАХ ───────────────────────────
    # PoemCard ♥
    (
        "♥ button hidden in PoemCard",
        "        e('button', { className:'fav '+(fav?'active':''), onClick: onFav, title:'В избранное' }, e(Icon.heart, null, fav)),\n",
        "",
        1,
    ),
    # QuoteCard ♥
    (
        "♥ button hidden in QuoteCard",
        "        e('button', { className:'fav', onClick: onFav, style:{ width:34, height:34, display:'grid', placeItems:'center', color: fav?'var(--alert)':'var(--ink-3)' } }, e(Icon.heart, null, fav)),\n",
        "",
        1,
    ),
    # Library: ♥ у поэта
    (
        "♥ button hidden in Library poet head",
        """          e('button', { className:'fav-btn '+(favs.has('author::'+poet.name)?'active':''),
              onClick: ev => { ev.stopPropagation(); toggleFav('author::'+poet.name); } },
              e(Icon.heart, null, favs.has('author::'+poet.name))),
""",
        "",
        1,
    ),

    # ── СПРЯТАТЬ КНОПКИ ЗАПИСИ В FRAME-TOOLS-MINI ────────────────
    # Убираем ♥, ✏️ Редактировать, 🗑 Удалить — оставляем только ⧉ Копировать
    (
        "frame-tools-mini: keep only copy",
        """        e('div', { className:'frame-tools-mini' },
          e('button', { title: favs.has(f.id)?'В избранном':'В избранное',
            className: favs.has(f.id)?'ft-btn on':'ft-btn', onClick: () => toggleFav(f.id) }, favs.has(f.id)?'♥':'♡'),
          e('button', { title:'Редактировать', className:'ft-btn', onClick: () => setEditingFrame(f) }, e(Icon.edit)),
          e('button', { title:'Копировать описание', className:'ft-btn', onClick: () => copy(f.why || '') }, e(Icon.copy)),
          e('button', { title:'Удалить', className:'ft-btn danger', onClick: () => deleteFrame(f.id) }, e(Icon.trash))))),""",
        """        e('div', { className:'frame-tools-mini' },
          e('button', { title:'Копировать описание', className:'ft-btn', onClick: () => copy(f.why || '') }, e(Icon.copy))))),""",
        1,
    ),

    # ── PoemModal: убрать ♥ и «Вписать вручную» ─────────────────
    (
        "PoemModal: remove fav and edit-manually buttons",
        """        !editing && e('button', { className: 'btn '+(fav?'primary':''), onClick: onFav }, e(Icon.heart, null, fav), fav?'В избранном':'В избранное'),
        !editing && poem.link && e('button', { className:'btn', onClick: startEdit }, e(Icon.edit), 'Вписать вручную'),
""",
        "",
        1,
    ),

    # ── Спрятать кнопку «Добавить кадр» (3 места) ────────────────
    (
        "Cinema empty state: hide 'Add frame'",
        """          : e('button', { className:'btn primary', onClick: () => setAddOpen(true) }, e(Icon.plus), 'Добавить кадр')),""",
        """          : null),""",
        1,
    ),
    (
        "Cinema roulette actions: hide 'Add frame'",
        """    e('div', { className:'actions', style:{ margin:'20px auto 0', justifyContent:'center' } },
      e('button', { className:'btn', onClick: () => setAddOpen(true) }, e(Icon.plus), 'Добавить кадр')));""",
        "    null);",
        1,
    ),
    (
        "Cinema gallery toolbar: hide 'Add frame', keep 'Roulette'",
        """      e('div', { className:'actions' },
        e('button', { className:'btn primary', onClick: () => { setSubSection('roulette'); shuffle(); } },
          e('span', { className:'glyph' }, '✦'), 'Рулетка'),
        e('button', { className:'btn', onClick: () => setAddOpen(true) }, e(Icon.plus), 'Добавить кадр'))),""",
        """      e('div', { className:'actions' },
        e('button', { className:'btn primary', onClick: () => { setSubSection('roulette'); shuffle(); } },
          e('span', { className:'glyph' }, '✦'), 'Рулетка'))),""",
        1,
    ),

    # ── Меню «Настройки» в Roulette (Обновить/Скачать тексты/Стоп/Папка) ─
    (
        "Roulette: remove server-side settings menu",
        """      e('details', { className:'overflow-menu' },
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
              e('span', { className:'h' }, 'csv, json, бэкап')))))),""",
        "      null),",
        1,
    ),

    # ── EmptyState первой загрузки (когда база пуста — кнопка «Обновить базу») ─
    (
        "Empty state: drop 'Обновить базу' button",
        """          : e('button', { className:'btn primary', onClick: runUpdate, disabled: updating },
              e('span', { className:'glyph' }, '↻'), updating ? (updateStage || 'работаю…') : 'Обновить базу')));""",
        """          : e('button', { className:'btn', onClick: () => setCategory('all') }, 'База ещё подгружается…')));""",
        1,
    ),

    # ── Спрятать подтаб ♥ Избранное ──────────────────────────────
    # Литература
    (
        "Subnav literature: drop 'Избранное' tab",
        """              {id:'roulette',label:'Рулетка'},
              {id:'library',label:'Библиотека поэтов'},
              {id:'favorites',label:'♥ Избранное' + (favs.size ? ' · ' + favs.size : '')}
            ], active: litSub, onChange: navLitSub }),""",
        """              {id:'roulette',label:'Рулетка'},
              {id:'library',label:'Библиотека поэтов'}
            ], active: litSub, onChange: navLitSub }),""",
        1,
    ),
    # Кино
    (
        "Subnav cinema: drop 'Избранное' tab",
        """    {id:'roulette',label:'Рулетка'},
    {id:'gallery',label:'Галерея'},
    {id:'favorites',label:'♥ Избранное' + (data.FRAMES.filter(f=>favs.has(f.id)).length ? ' · ' + data.FRAMES.filter(f=>favs.has(f.id)).length : '')},
  ];""",
        """    {id:'roulette',label:'Рулетка'},
    {id:'gallery',label:'Галерея'},
  ];""",
        1,
    ),

    # ── Категория 'fav' в фильтрах рулетки ───────────────────────
    (
        "Drop 'Избранное' from category filters",
        """    { id: 'all', label: 'Всё', count: usablePoems.length + QUOTES.length },
    { id: 'fav', label: 'Избранное', count: 0 },
  ];""",
        """    { id: 'all', label: 'Всё', count: usablePoems.length + QUOTES.length },
  ];""",
        1,
    ),

    # ── Footer: убрать счётчик «в избранном» ────────────────────
    (
        "Footer: drop favs counter",
        """        e('span', null, e('strong', null, data.FRAMES.length), ' кадров'),
        e('span', null, e('strong', null, favs.size), ' в избранном')),""",
        """        e('span', null, e('strong', null, data.FRAMES.length), ' кадров')),""",
        1,
    ),

    # ── Обновить title и meta-описание для публичной версии ──────
    (
        "Update <title> for public version",
        "<title>Сеанс</title>",
        "<title>Сеанс — литературно-визуальная рулетка</title>\n<meta name=\"description\" content=\"Не библиотека — рулетка для вдохновения. Стих, цитата или кадр из кино, выпавшие наугад. Не ищите — пусть случай выберет за вас.\"/>",
        1,
    ),

    # ── Pages: при каждом заходе открываем «Литература → Рулетка» ──
    # В приложении выбор раздела/подраздела запоминается в localStorage,
    # это удобно для одного владельца. На публичной версии — наоборот:
    # случайный посетитель должен сразу попадать в рулетку, даже если
    # в прошлый визит сам клацнул на библиотеку. Тема (light/dark)
    # остаётся персистентной — это про визуальный комфорт, не навигацию.
    (
        "Pages: всегда открываем «Литература → Рулетка»",
        """  const [section, setSection] = useState(() => localStorage.getItem('seance.section') || 'lit');
  const [litSub, setLitSub] = useState(() => localStorage.getItem('seance.litSub') || 'roulette');
  const [cineSub, setCineSub] = useState(() => localStorage.getItem('seance.cineSub') || 'roulette');""",
        """  const [section, setSection] = useState(() => 'lit');
  const [litSub, setLitSub] = useState(() => 'roulette');
  const [cineSub, setCineSub] = useState(() => 'roulette');""",
        1,
    ),
]


def transform(html: str) -> str:
    for desc, old, new, expected in EDITS:
        cnt = html.count(old)
        if cnt != expected:
            sys.stderr.write(
                f"[ОШИБКА] «{desc}»: ожидалось вхождений = {expected}, найдено = {cnt}\n"
                f"  Возможно, HTML_PAGE в app.py изменился. Проверь правки.\n"
            )
            sys.exit(2)
        html = html.replace(old, new, expected)
        print(f"  ✓ {desc}")
    return html


# ─────────────────────────────────────────────────────────────────
# 3. Snapshot данных через работающий сервер (опционально)
# ─────────────────────────────────────────────────────────────────

def http_get(url: str, dest: Path) -> None:
    print(f"  GET {url} → {dest.name}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        dest.write_bytes(resp.read())


def take_snapshot() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        http_get(SERVER_URL + "/api/poems", OUT_DIR / "data.json")
        http_get(SERVER_URL + "/api/screenshots", OUT_DIR / "screenshots.json")
    except Exception as e:
        sys.stderr.write(
            f"\n[snapshot] Не получилось — сервер не отвечает на {SERVER_URL}.\n"
            f"  Запусти приложение Сеанс или: ASKBUKA_GUI=0 python3 app.py\n"
            f"  Ошибка: {e}\n"
        )
        sys.exit(2)

    src_screens = DATA_DIR / "screenshots"
    dst_screens = OUT_DIR / "screenshots"
    if src_screens.exists():
        if dst_screens.exists():
            shutil.rmtree(dst_screens)
        shutil.copytree(src_screens, dst_screens)
        n = sum(1 for _ in dst_screens.iterdir())
        print(f"  cp -R screenshots/ → docs/screenshots/  ({n} файлов)")
    else:
        print(f"  [warn] папка с кадрами не найдена: {src_screens}")


# ─────────────────────────────────────────────────────────────────
# 3.5. Обогащение data.json текстами из локального корпуса
# ─────────────────────────────────────────────────────────────────
#
# В poems.json у части записей лежит только excerpt + link, без полного
# text. В десктопе модалка догружает текст через /api/poem (там вызывается
# _try_corpus). На статике /api/poem заглушен — поэтому полный текст надо
# вписать прямо в data.json на этапе сборки.

def enrich_data_json() -> None:
    target = OUT_DIR / "data.json"
    if not target.exists():
        print(f"  [skip enrich] {target.name} нет — пропускаю")
        return

    sys.path.insert(0, str(ROOT))
    try:
        import app  # type: ignore
    except Exception as e:
        sys.stderr.write(f"  [enrich] не удалось импортировать app.py: {e}\n")
        return

    print("→ Обогащаю data.json текстами из корпуса…")
    with target.open("r", encoding="utf-8") as f:
        data = json.load(f)

    app.ensure_corpus_loaded()
    if not app._CORPUS_INDEX:
        print("  [warn] корпус пустой — нечего подмешивать")
        return

    enriched = 0
    missing = 0
    for item in data:
        if item.get("type") != "poem":
            continue
        if item.get("text"):
            continue
        text = app._try_corpus(item)
        if text:
            item["text"] = text
            enriched += 1
        elif item.get("link"):
            missing += 1

    if enriched == 0:
        print(f"  ничего не добавил (без текста: {missing})")
        return

    with target.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"  ✓ обогащено: {enriched}  ·  без текста осталось: {missing}  ·  размер: {size_mb:.1f} МБ")


# ─────────────────────────────────────────────────────────────────
# 4. Точка входа
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Сборка статической версии Сеанса для GitHub Pages")
    p.add_argument("--snapshot", action="store_true",
                   help="Перед сборкой снять свежий snapshot данных через работающий app.py")
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.snapshot:
        print("→ Снимаю snapshot данных из работающего сервера…")
        take_snapshot()

    enrich_data_json()

    print("→ Извлекаю HTML_PAGE из app.py…")
    src = APP_PY.read_text(encoding="utf-8")
    html = extract_html_page(src)

    print(f"→ Применяю {len(EDITS)} правок:")
    html = transform(html)

    out = OUT_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"\n✓ Готово: {out}  ({size_kb:.1f} КБ)")

    # Sanity-check: data.json и screenshots.json должны лежать рядом
    for name in ("data.json", "screenshots.json"):
        f = OUT_DIR / name
        if not f.exists():
            print(f"  [warn] {name} не найден в {OUT_DIR}. Запусти build_static.py --snapshot")


if __name__ == "__main__":
    main()
