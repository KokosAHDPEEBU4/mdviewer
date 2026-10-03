# База данных и источники записей

> Кто и что пишет в БД. Справочник для размышлений об улучшениях:
> если какой-то компонент пишет, это часть поверхности разработки
> и потенциальная точка роста.

## 1. Какая БД

| Окружение | Движок | URL / файл | Где задано |
|---|---|---|---|
| Dev | SQLite + aiosqlite | `./messenger.db` (один файл) | `DB_URL` (дефолт `app/config.py:79`) |
| Prod | PostgreSQL + asyncpg | `postgresql+asyncpg://...` (volume) | `DB_URL` в env / `.env` |
| Тесты | SQLite in-memory | память (fixture в `tests/conftest.py`) | `tests/` |
| Тесты (Postgres) | PostgreSQL 16-alpine | порт 5433, tmpfs | `docker-compose.test.yml` |

- Схема создаётся **только** через Alembic: при старте приложения в
  lifespan выполняется `alembic upgrade head` (`app/main.py:110-111`).
  `Base.metadata.create_all` не используется. Каждая из 23+ миграций —
  в `alembic/versions/`.
- Физически на диск (мимо БД) пишутся: медиа-файлы (`MEDIA_PATH/media/`),
  APK (`RELEASES_PATH/releases/`), Opus-кэш музыки (`music/opus_cache/`),
  файл отчёта метрик (`scripts/metrics.json`).

## 2. Как код ходит в БД

| Потребитель | Механизм | Коммит |
|---|---|---|
| REST-роуты | `db: AsyncSession = Depends(get_db)` (`app/database.py:50`) | автоматический при `yield`, `rollback` при исключении |
| WebSocket | `async with AsyncSessionLocal() as db:` напрямую (`app/ws.py`) | явный `await db.commit()` |
| Фоновые таски (lifespan) | `async with AsyncSessionLocal()` (scheduled ticker, VAPID, реакции) | явный commit |
| Внешние процессы | `create_admin.py`, `set_superadmin.py` — отдельные сессии | явный commit |

`expire_on_commit=False` — после коммита ORM-объекты остаются валидными
для чтения (в async без lazy-load после закрытия сессии).

## 3. Карта записей: кто и что пишет

### 3.1. REST-роуты (`app/routes/`)

| Таблица | Роут | Операция |
|---|---|---|
| `users` | `auth.py` | register (создание), login (last_seen_at нет), смена пароля |
| `users` | `users.py` | update profile, admin: block/unblock, role_change, reset password |
| `user_settings` | `users.py`, `notifications/router.py` | профиль; тумблер `notifications_enabled` |
| `refresh_tokens` | `auth.py` | login/register (создание), refresh (reuse-detection), revoke |
| `password_reset_tokens` | `auth.py` | forgot-password (создание), reset (одноразовый, revoke) |
| `chats` | `chats.py` | create direct/group; delete (soft через audit) |
| `chat_members` | `chats.py` | join/add/remove/leave, hide, mute, favorite, pin |
| `invites` | `invites.py` | create, use (used_by), delete |
| `messages` | `messages.py` | send, forward, edit, soft-delete, pin |
| `message_reactions` | `messages.py` | `toggle_reaction` (add/remove) |
| `media_views` | `messages.py` | фиксация «начал/закончил» прослушивания/просмотра |
| `file_metadata` | `messages.py` | при отправке медиа (через `ffprobe`, `app/file_meta.py`) |
| `scheduled_messages` | `scheduled.py` | create, edit (`text`/`scheduled_at`), cancel (`status=cancelled`), send (`status="sent"`, `message_id`, `sent_at`, `attempts`/`last_error`) |
| `reactions_whitelist` | `cache.py` / admin-эндпоинт | авто-сид 25 дефолтных при пустой таблице (lifespan) |
| `music_*` | `music.py` | favorites, history, requests, playback_sessions, queue, playlists, playlist_items, quality_overrides, lyrics |
| `app_releases` | `app.py` | admin: POST (новый релиз), DELETE |
| `audit_logs` | `audit.py` / роуты | `log_audit` из `auth.py:604`, `users.py:253,376,422,629`, `chats.py:1016`, `messages.py:791` |
| `activity_events` | `auth.py`, `messages.py`, `chats.py` | `record_activity` (register, login, message_sent, chat_created) |

**Важно:** `upload.py` **не пишет в БД** — только сохраняет файл на диск
(валидация, MIME, лимиты). Само сообщение создаётся позже через
`POST /api/chats/{id}/messages` с `file_url` от upload'а.

### 3.2. WebSocket (`app/ws.py`)

| Таблица | Где | Операция |
|---|---|---|
| `users.last_seen_at` | `ws.py:254` (connect), `ws.py:301` (disconnect) | обновляется при каждом подключении/отключении |
| `chat_read_states` | `_handle_read` (`ws.py:491`) | upsert позиции прочтения (user_id, chat_id) |
| `message_reads` | `_handle_read` (`ws.py:528`) | по строке на каждое впервые прочитанное сообщение |
| `messages.status = "read"` | `_handle_read` (`ws.py:532`) | денормализованный глобальный статус |

### 3.3. Фоновые процессы (lifespan, `app/main.py:54`)

| Источник | Когда | Что пишет |
|---|---|---|
| `alembic upgrade head` | старт | всю схему |
| `load_reactions_cache()` | старт | `reactions_whitelist` (сид, если пусто) |
| `ensure_vapid_keys()` (`app/notifications/vapid.py:37`) | старт | `vapid_keys` (авто-генерация пары, если не задана в `.env`) |
| scheduler loop `process_scheduled_messages()` | каждые `SCHEDULED_INTERVAL_SECONDS` (30 по умолчанию) | `messages` (создание due-сообщений) + `scheduled_messages.status="sent"` + `message_id`/`sent_at` (пакетом до `SCHEDULED_BATCH_LIMIT`, ORDER BY `scheduled_at`, в одной транзакции — защита от дублей); при сбое — `attempts+1`, `last_error` |
| notification bus consumer (`notifications/dispatch.py`) | после событий | **ничего не пишет** — только читает подписки и отправляет push |

### 3.4. Внешние клиенты поставки данных

| Таблица | Кто | Операция |
|---|---|---|
| `push_subscriptions` | SPA/браузер | `POST /api/notifications/subscribe\|unsubscribe` |
| `fcm_tokens` | Android-приложение | `POST /api/notifications/fcm/subscribe\|unsubscribe` (переназначение токена между пользователями) |
| `users.role` | Админ/суперадмин | `users.py` (роли), `scripts/set_superadmin.py` |

### 3.5. Только читающие (не писатели)

- `notifications/dispatch.py` — читает подписки для рассылки.
- `routes/metrics.py` — `/api/metrics`, admin dashboard: только чтение/агрегация.
- `scripts/metrics_worker.py` — читает БД, но **пишет в `scripts/metrics.json`**, не в БД.
- `routes/health.py` — только healthcheck.
- Музыкальные треки/метаданные музыки — **не в БД**: реестр треков живёт
  в памяти (кэш `scan_music_dir`); БД содержит только user-state (`track_id`
  строками, любимое/история/очередь/плейлисты/качество/тексты).

## 4. Распределение «горячих» записей

Самые частые DB-записи в работающем сервисе:

1. `message_reads` + `chat_read_states` + `messages.status="read"` — по одному
   событию `read` приходится от 1 до N+2 записей (N — число впервые
   прочитанных сообщений).
2. `users.last_seen_at` — на каждый WS-connect/disconnect (включая
   переподключения при нестабильной сети).
3. `messages` — на каждое отправленное сообщение (3 состояния доставки потом).
4. `activity_events` — на register/login/message_sent/chat_created.

## 5. Наблюдения и идеи для улучшений

1. **`last_seen_at` — write-on-connect.** Обновление на каждый reconnect —
   лишняя запись в горячей строке `users`. Статус «сейчас онлайн» уже есть в
   памяти (`ConnectionManager.is_online`). Идея: для списка чатов отдавать
   статус из памяти, а `last_seen_at` в БД проставлять реже (например, в
   heartbeat раз в минуту или раз в 5 минут).

2. **Три записи на одно прочтение.** `message_reads` растёт без ограничений
   (строка на каждое сообщение×читателя). Идеи: (а) retention-чистка старых
   строк; (б) оставить только `chat_read_states` (позиция) и материализовать
   «прочитано до здесь» на лету; (в) для модалки «кто прочитал» — выбирать
   только при открытии (уже так), хранить с TTL.

3. **Нет фоновой гигиены таблиц.** Нет cleanup-задачи для
   `refresh_tokens` (истёкшие), `password_reset_tokens`, `activity_events`,
   `message_reads`, `audit_logs`. Сейчас данные только растут. Идея:
   фоновый воркер (как scheduler loop, каждые N часов) с retention-политикой
   (удалять expired / старше N дней). Каскады покрывают только удаление юзера.

4. **Денормализация `messages.status`.** Docs-примечание: «read» хранится
   на `Message`, чтобы `list_chats` делал один выбор без JOIN. Это осознанно,
   но статус пишется при каждом прочтении каждым читателем — потенциально
   «горячая» колонка. При росте чата рассмотреть `read-receipts` на позиции
   вместо статуса на каждом сообщении.

5. **Миграции на старте приложения.** `alembic upgrade head` при каждом
   старте — пока одна реплика, ок. При горизонтальном масштабировании —
   вынести в отдельный деплой-шаг (см. docs/operations.md про инфраструктуру).

6. **Индексы на горячих FK.** Часть индексов уже есть (`ix_*` в миграциях).
   Для больших `messages` стоит проверить покрытие запросов: chat_id+sender_id
   для `list_chats`/`loadMessages`; партиционирование по дате — если чат
   вырастет (идея из [data-model.md](data-model.md)).

7. **Единый писатель.** В БД пишет только app-процесс (async) + редкие
   админ-скрипты; `metrics_worker` и push-диспетчер не пишут. Это упрощает
   любые будущие изменения (бэтч-записи, outbox, репликация с одного
   master-инстанса).

8. **Read vs write по таблицам.** Доминирует чтение (`messages`,
   `chat_members`, `chat_read_states`, `music_*`). Записи концентрируются в
   событийном слое (`activity_events`, `audit_logs`, `message_reads`) —
   самые перспективные кандидаты на агрегацию/архив.

## Связанные документы

- [data-model.md](data-model.md) — схема БД, модели, каскады, миграции.
- [architecture.md](architecture.md) — потоки данных, WS vs REST.
- [operations.md](operations.md) — деплой, БД-бэкапы, мониторинг.
- [api.md](api.md) — REST API reference (какие эндпоинты что меняют).