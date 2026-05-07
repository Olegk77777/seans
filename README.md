# Сеанс

Литературно-визуальная рулетка. Локальное приложение на macOS — случайные стихи, цитаты, кадры из кино.

**Веб-версия (только просмотр):** [olegk77777.github.io/seans](https://olegk77777.github.io/seans/)

## Что внутри

- `app.py` — десктопное приложение (Python HTTP-сервер + pywebview).
- `build_static.py` — сборщик статической версии для GitHub Pages.
- `docs/` — статическая версия (`index.html` + `data.json` + кадры). Это корень GitHub Pages.
- `Сеанс.app/` — macOS-бандл-лончер.
- `pdf_to_csv.py` / `wikiquote_fetch.py` / `wayback_drain.py` — служебные скрипты для пополнения базы.
- `CLAUDE.md` — рабочие заметки по проекту.

## Десктоп-версия

```
Сеанс.app/Contents/MacOS/Seans
```

Лончер ставит venv и запускает `app.py` в окне pywebview. Данные живут в `~/Library/Application Support/Сеанс/data/`.

## Веб-версия (GitHub Pages)

В Pages-версии работают: рулетка, библиотека поэтов, поиск, кино-галерея, копирование в буфер, переключение тёмной/светлой темы. Запись отключена — никаких «Обновить базу», «Добавить кадр», «Избранное».

### Пересобрать после правок

```
# 1. Запусти десктоп-версию (или: ASKBUKA_GUI=0 python3 app.py)
# 2. Когда сервер на :8765 поднялся — пересобери статику и сними свежий snapshot:
python3 build_static.py --snapshot

# 3. Закоммить и запушь — Pages обновится через 1-2 минуты:
git add docs/ && git commit -m "Обновил базу" && git push
```

`build_static.py` без `--snapshot` пересобирает только `index.html` (быстро, не требует запущенного сервера).

### Настройки GitHub Pages

Settings → Pages → Source: `Deploy from a branch` → Branch: `main` → Folder: `/docs`.
