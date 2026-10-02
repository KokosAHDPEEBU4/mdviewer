# MD Viewer

Быстрый просмотрщик Markdown для Windows: PyQt6 + mistune, тёмная тема,
сборка в один `.exe` без установленного Python.

## Возможности

- тёмная тема, читаемая колонка текста, подсветка кода, цитаты, таблицы,
  task lists, сноски, определения;
- локальные картинки (`![](img/a.png)`) подставляются из папки документа,
  внешние ссылки открываются в браузере;
- поиск `Ctrl+F`, перезагрузка `F5`, масштаб `Ctrl`/`+`/`-`/`0`;
- запоминает размер/положение окна и папку последнего открытия;
- не пересобирает HTML при изменении размера окна (ширина колонки задаётся
  отступами `QTextBrowser.viewport()`, а не CSS) — resize крупного файла
  стоит ~14 мс вместо ~236 мс.

## Запуск из исходников

```bash
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py README.md
```

## Сборка

```bash
build.bat            # .venv + зависимости + PyInstaller -> dist\MDViewer
build.bat --run      # ... и сразу регистрация обработчика .md
```

`dist\MDViewer\MDViewer.exe` (~54 МБ) содержит свой интерпретатор
(`python314.dll`) и Qt — Python на машине не нужен.

## Ассоциация с .md

```bash
dist\MDViewer\MDViewer.exe --install-default
```

Приложение копируется в `%LOCALAPPDATA%\Programs\MDViewer`, регистрируется в
`HKCU\Software\Classes` (ProgID `MDViewer.MarkdownFile`, иконка, команда,
`OpenWithProgids`, `RegisteredApplications`) и открывает системный диалог
«Открыть с помощью» — там нужно один раз нажать «Всегда».

Назначить обработчик по умолчанию напрямую нельзя: ключ
`...\Explorer\FileExts\.md\UserChoice` принадлежит Explorer, и обычный процесс
не может его открыть на запись (`WinError 5`), несмотря на корректный hash.

Проверка и откат:

```bash
dist\MDViewer\MDViewer.exe --assoc-status
dist\MDViewer\MDViewer.exe --uninstall-default
```

## Структура

```
main.py        CLI, QApplication, диалог открытия файла
viewer.py      окно: QTextBrowser, поиск, shortcuts, настройки
renderer.py    mistune -> HTML (post-processing кода, картинок, чекбоксов)
theme.py       палитра, CSS для Qt rich text, размеры колонки
winassoc.py    регистрация обработчика .md в HKCU
version.py     версия, имя, ProgID, расширения
MDViewer.spec  PyInstaller: onedir, иконка, версия, вырезание лишнего Qt
tools/         make_icon.py (генерация assets/icon.ico), render_check.py,
               debug_html.py (быстрый просмотр HTML)
```

## Ограничения Qt, на которые заточен код

`QTextBrowser` понимает лишь подмножество CSS: не работают переменные,
`max-width`, `margin: auto`, `padding`/`border` у блочных элементов,
`::before`, переходы. Поэтому отступы колонки задаются через
`viewport().setContentsMargins()`, а блоки кода оборачиваются в `<table>`
(у ячеек padding работает). Таблица приложения должна быть установлена до
первого `setHtml()`, иначе Qt не применит стили к документу.

## Проверки

```bash
.venv\Scripts\python tools\render_check.py   # рендер всех стилей -> PNG
.venv\Scripts\python tools\debug_html.py    # HTML без запуска GUI
.venv\Scripts\python -m py_compile *.py
```