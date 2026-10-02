# AGENTS.md — как работать с проектом MD Viewer

Настольный просмотрщик Markdown для Windows. **UI:** PyQt6 (`QTextBrowser`),
**рендер:** mistune, **сборка:** PyInstaller onedir. Код Python 3.10+,
целевая среда — Windows.

## Структура

```
main.py         CLI (файлы, --install-default/--uninstall-default/--assoc-status),
                QApplication, диалог открытия файла
viewer.py       окно: QTextBrowser, поиск, shortcuts, zoom, QSettings
renderer.py     mistune -> HTML: post-processing кода, картинок, чекбоксов, сносок
theme.py        палитра, CSS для Qt rich text, MEASURE/side_margin
winassoc.py     регистрация .md в HKCU (ProgID, OpenWithProgids, RegisteredApplications)
version.py      APP_NAME, APP_VERSION, PROG_ID, EXTENSIONS — общий источник истины
MDViewer.spec   PyInstaller: иконка, версия, excludes, вырезание лишнего Qt
build.bat       venv + pip + PyInstaller, флаг --run
tools/          make_icon.py, render_check.py, debug_html.py
```

## Команды

```bash
.venv\Scripts\python main.py README.md          # запуск из исходников
build.bat                                       # сборка dist\MDViewer
.venv\Scripts\python tools\render_check.py      # визуальная проверка темы
.venv\Scripts\python -m py_compile *.py
```

## Инварианты (НЕ нарушать)

1. **Qt rich text поддерживает подмножество CSS.** Не работают: CSS-переменные,
   `max-width`, `margin: 0 auto`, `padding`/`border` у блочных элементов,
   `::before/::after`, переходы, внешние CSS-файлы. Любое новое оформление
   сначала проверяй рендером: `tools/render_check.py` (цвета) или
   `tools/debug_html.py` (структура HTML).

2. **Ширина колонки — через `QTextBrowser.viewport().setContentsMargins()`**
   (`theme.side_margin`, `viewer.Viewer._set_margins`), а не через CSS: пересборка
   HTML на каждый resize стоила ~236 мс, отступы — ~14 мс. HTML собирается один
   раз в `Viewer.load()`.

3. **Блоки кода рендерятся таблицей** (`renderer._code_block`), потому что у
   `<table><td>` в Qt работает `padding`, а у `<pre>` — нет. Регулярка
   `_CODE_BLOCK` обязана захватывать `</code>` в группу: если оставить его вне
   группы, в вывод попадёт «висячий» закрывающий тег.

4. **`mistune` работает с `escape=False`.** Это приложение для локального
   просмотра файлов; включай новые плагины только если проверишь, что они не
   ломают вывод (см. список плагинов в `renderer.py`).

5. **Никаких сетевых запросов и внешних ресурсов.** Ссылки открываются через
   `QDesktopServices`, картинки — только локальные (`file:` URI), ширина
   ограничивается через `QImageReader`.

6. **Ассоциацию .md нельзя выставить программно.** Ключ
   `HKCU\...\Explorer\FileExts\.md\UserChoice` открывается на чтение, запись
   даёт `WinError 5` — Explorer пишет его сам, только после действия
   пользователя (`SHOpenWithDialog` или «Параметры → Приложения по умолчанию»).
   `winassoc.install()` регистрирует всё остальное и открывает диалог; ключ
   с hash из старых версий проекта удалён — не возвращай его.

7. **Сборка только из `.venv` и всегда чистая.** `--clean` обязателен: старые
   артефакты `python310.dll` от прошлых сборок иначе попадут в `dist` (именно
   это вызывало ошибку «не найден python310.dll»). Проверяй результат:
   `dist\MDViewer\_internal\python314.dll` есть, `python310.dll` — нет.

8. **Собирай версию из объекта `VSVersionInfo`**, не из словаря: PyInstaller
   6.22 отклоняет dict (`TypeError: Unsupported type for version info
   argument`). Структура собирается в `MDViewer.spec` из
   `PyInstaller.utils.win32.versioninfo`.

## Что НЕ делать (грабли)

- ❌ Не добавляй зависимости без записи в `requirements.txt` (собирается только
  он) и без проверки `build.bat`.
- ❌ Не редактируй файлы PowerShell-скриптом с `Get-Content`/`Set-Content` без
  `-Encoding UTF8`: кириллица в Python-файлах уже ломалась дважды.
- ❌ Не удаляй `qschannelbackend.dll`/Qt-плагины «на всякий случай»: картинки
  грузятся через `imageformats`. Исключать можно только то, что действительно не
  нужно (`DROP_BINARIES` в spec — с комментарием, почему).
- ❌ Не коммить `dist/`, `build/`, `.venv/`, `__pycache__/` (см. `.gitignore`).
- ❌ Не оставляй в репозитории `AGENTS.md` от другого проекта — если файл не
  описывает MD Viewer, перезаписывай его.

## Что делать

- ✅ Новое оформление → сначала `tools/render_check.py`, потом сборка и
  визуальная проверка окна.
- ✅ Новая опция рендера → добавить кейс в `tools/render_check.py` и в
  `tools/debug_html.py`.
- ✅ Новая CLI-команда → `main.py: main()` + строка в `USAGE`.
- ✅ Перед коммитом: `python -m py_compile *.py` и `build.bat`.
- ✅ В конце ответа предлагай название коммита в формате
  `<scope>: <что сделано>` (conventional commits). Один коммит — одна тема.