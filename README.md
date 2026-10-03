# MD Viewer

Быстрый просмотрщик Markdown для Windows: PyQt6 + mistune, тёмная тема,
сборка в один `.exe` без установленного Python.

## Возможности

- тёмная тема в духе QuickLook, читаемая колонка текста, подсветка кода,
  цитаты, таблицы, task lists, сноски, определения;
- оглавление слева: разделы с отступами по уровням, текущий раздел подсвечивается
  при прокрутке, клик переходит к разделу (`Ctrl+B` скрывает/показывает);
- всплывающая панель управления: появляется при наведении на окно и содержит
  оглавление, режим правки, поиск, открытие файла и масштаб;
- локальные картинки (`![](img/a.png)`) подставляются из папки документа,
  внешние ссылки открываются в браузере;
- ссылки на другие `.md`/`.markdown`/`.txt` открываются в этом же окне с полным
  оформлением, кнопка «←» в панели (или `Alt+Left`) возвращает в предыдущий файл;
- поиск `Ctrl+F`, перезагрузка `F5`, масштаб `Ctrl`/`+`/`-`/`0`;
- режим правки: `Ctrl+E` открывает справа исходный Markdown (окно расширяется,
  колонки делит `QSplitter`), кнопки «Конвертировать» (`Ctrl+Enter`) и
  «Сохранить» (`Ctrl+S`); после сохранения превью обновляется само,
  `•` в заголовке означает несохранённые изменения, `Esc` выходит из правки;
- кодировка и переводы строк исходного файла сохраняются при записи
  (utf-8 с/без BOM, utf-16, cp1251, LF или CRLF);
- тёмный системный заголовок окна (`DwmSetWindowAttribute`);
- запоминает размер/положение окна и папку последнего открытия; при закрытии
  из режима правки сохраняется геометрия окна просмотра, а не расширенного;
- в заголовке окна всегда видно `— MD Viewer <версия>`: видно, какая сборка
  запущена;
- не пересобирает HTML при изменении размера окна (ширина колонки задаётся
  отступами `QTextBrowser`, а не CSS) — resize крупного файла стоит ~14 мс
  вместо ~236 мс; оглавление строится в том же проходе, что и рендер
  (+~40 мс на 500 заголовков), а позиции разделов считаются лениво.

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
viewer.py      окно: превью, оглавление, редактор, оверлей, поиск, shortcuts
renderer.py    mistune -> HTML (код, картинки, чекбоксы, якоря заголовков)
theme.py       палитра, CSS для Qt rich text, размеры колонки, QSS приложения
winassoc.py    регистрация обработчика .md в HKCU
version.py     версия, имя, ProgID, расширения
MDViewer.spec  PyInstaller: onedir, иконка, версия, вырезание лишнего Qt
tools/         make_icon.py (генерация assets/icon.ico), render_check.py,
               debug_html.py (быстрый просмотр HTML),
               edit_check.py (правка/оглавление/оверлей без пользователя)
```

## Ограничения Qt, на которые заточен код

`QTextBrowser` понимает лишь подмножество CSS: не работают переменные,
`max-width`, `margin: auto`, `padding`/`border` у блочных элементов,
`::before`, переходы. Поэтому отступы колонки задаются через
`viewport().setContentsMargins()`, а блоки кода оборачиваются в `<table>`
(у ячеек padding работает). Таблица приложения должна быть установлена до
первого `setHtml()`, иначе Qt не применит стили к документу.

Второй подвох Qt — `QPlainTextEdit.toPlainText()` всегда возвращает текст с
`\n`, даже если в файле `\r\n`. Поэтому сравнение «правился ли файл»
(`viewer._baseline`, `viewer._rendered`) идёт через `viewer._normalized()`,
а не напрямую с `read_document()`.

## Проверки

```bash
.venv\Scripts\python tools\render_check.py   # рендер всех стилей -> PNG
.venv\Scripts\python tools\debug_html.py    # HTML без запуска GUI
.venv\Scripts\python tools\edit_check.py    # правка/конвертация/сохранение
.venv\Scripts\python -m py_compile *.py
```