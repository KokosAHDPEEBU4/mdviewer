# Архитектура

## Высокоуровневая схема

```
┌────────────────────────────────────┐
│  Frontend (vanilla JS SPA)         │
│  ┌──────────────────────────────┐  │
│  │  app/static/js/  (25 подкл.)   │  │
│  │   globals → auth → chats →   │  │
│  │   msg-* → media → ws → ...   │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  app/static/css/app.css      │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  sw.js (Service Worker)      │  │
│  │   network-first + cache      │  │
│  └──────────────────────────────┘  │
└───────────────┬────────────────────┘
                │ REST /api/*
                │ WS   /ws
                │ Static /static/*
                │ Media /media/{uuid}
┌───────────────▼────────────────────┐
│  FastAPI (uvicorn, async)          │
│  ┌──────────────────────────────┐  │
│  │  Routes                      │  │
│  │   auth, users, chats,        │  │
│  │   messages, invites,         │  │
│  │   upload, scheduled,         │  │
│  │   health, music, metrics     │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  Notifications               │  │
│  │   router, sender, dispatch   │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  WebSocket (ws.py)           │  │
│  │   ConnectionManager          │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  Deps (auth context)         │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  Auth (JWT)                  │  │
│  │   bcrypt(12) + PyJWT HS256   │  │
│  └──────────────────────────────┘  │
│  ┌──────────────────────────────┐  │
│  │  SQLAlchemy 2.0 (async)      │  │
│  │   asyncpg / aiosqlite        │  │
│  └──────────────────────────────┘  │
└──────────────┬─────────────────────┘
               │ asyncpg / aiosqlite
┌──────────────▼─────────────────────┐
│  PostgreSQL / SQLite               │
└────────────────────────────────────┘
```

Подробности по фронтенду — [frontend.md](frontend.md).

## Ключевые решения

### 1. Асинхронность end-to-end
- FastAPI + uvicorn в async-режиме
- SQLAlchemy 2.0 (async ORM, async session)
- asyncpg для Postgres, aiosqlite для dev
- Все I/O — неблокирующие

Это позволяет держать сотни одновременных WS-соединений
на одном воркере (в отличие от WSGI-стека).

### 2. Два транспорта параллельно

| Транспорт | Назначение | Примеры |
|---|---|---|
| **REST** | Все операции записи/чтения с гарантией доставки; запросы, меняющие состояние | login, send_message, delete_message |
| **WebSocket** | Push-уведомления в реальном времени, иначе клиенту пришлось бы polling'ить | new_message, typing, online/offline, message_edited |

REST и WS дополняют друг друга: REST — это команда ("сделай"), WS —
это уведомление ("у других произошло"). Например:
1. Клиент A делает `POST /api/messages` (REST).
2. Сервер создаёт Message, коммитит в БД.
3. Сервер рассылает `manager.broadcast_to_chat(chat_id, "new_message", ...)` всем участникам через WS.

Без WS клиенты B, C, D узнали бы о сообщении только при следующем
`GET /api/chats/{id}/messages`. С WS — мгновенно.

### 3. Аутентификация — двойная

- **Access-токен** (15 мин): в `Authorization: Bearer <jwt>`, stateless
- **Refresh-токен** (30 дней): в HttpOnly+Secure+SameSite=Lax cookie на `/api/auth`

См. [auth.md](auth.md) подробно.

### 3.1. Система ролей

Шестизначная иерархия (0-5): readonly → user → power → moderator → admin → superadmin.
Новый пользователь по умолчанию — level 2 (power). Лимиты инвайтов:
3 шт./нед. для level 2, без лимита для level 3+.

См. [roles.md](roles.md) подробно (ТЗ, конфиг, миграция, эндпоинты).

### 4. Soft-delete вместо hard-delete
- Сообщения: `deleted_at` timestamp (для thread continuity и аудита)
- Пользователи: hard delete (но `sender_id` ставится в NULL — SET NULL)
- Refresh-токены: hard delete

### 5. Монотонный ID сообщений
`Message.id` — это **не UUID**, а `{ms}_{seq}_{proc}` (время + монотонный
счётчик + токен процесса), см. `new_message_id()` в `app/models.py`. Единый
генератор используется для ВСЕХ типов сообщений (обычные, пересланные,
системные, отложенные). Это даёт:
- `ORDER BY created_at, id` = хронологически (id — tiebreaker; `String(36)`
  позволяет хранить legacy UUID v4, но новые сообщения всегда монотонны);
- read-логику и pagination без ложной предпосылки «UUID сортируется по времени»;
- 14 цифр ms хватит до 5138 года.
- Ограничение multi-worker: межпроцессного порядка нет (при равных `ms`+`seq`
  сортируется `proc`-токен), но коллизий PK нет — токен уникален на процесс.

### 6. Адресная WS-рассылка
Все события идут через `manager.broadcast_to_chat(chat_id, payload, member_ids)`,
где `member_ids` — список UUID участников. Широковещательной рассылки "всем"
нет — каждый получает только то, что касается его.

## Структура модулей

```
app/
├── main.py              ─ точка входа, lifespan, middleware, CORS
├── config.py            ─ pydantic-settings (Singleton, lru_cache, 50+ полей)
├── database.py          ─ engine + AsyncSessionLocal + get_db (Depends)
│
├── models.py            ─ 28 ORM-моделей + utcnow_naive(), new_message_id()
├── auth.py              ─ bcrypt + JWT + BLAKE2b login hash
├── deps.py              ─ Depends: get_token, get_current_user, get_admin_user, require_role
│
├── ws.py                ─ WebSocket: ConnectionManager, handlers, typing, session revocation
├── cache.py             ─ reactions whitelist cache (load_reactions_cache)
├── log.py               ─ structlog конфигурация (Console/JSON renderer)
├── audit.py             ─ log_audit() helper для записи аудит-событий
├── csp.py               ─ Content-Security-Policy middleware
├── email.py             ─ email-сервис (console/smtp/sendgrid)
├── file_meta.py         ─ ffprobe: метаданные медиа (длительность, кодек, размеры)
├── lrc_parser.py        ─ LRC-парсер для текстов песен
├── metrics.py           ─ in-memory metrics collector + Prometheus exposition
├── music_transcode.py   ─ Opus-транскодирование через ffmpeg
├── rate_limit.py        ─ Rate limiting (slowapi)
│
├── routes/              ─ тонкие HTTP-handlers, бизнес-логика inline
│   ├── auth.py          ─ /api/auth/* (register, login, refresh, logout, forgot/reset password)
│   ├── users.py         ─ /api/users/* (profile, search, role, block, admin reset)
│   ├── chats.py         ─ /api/chats/* (CRUD, members, pins, favorite, mute, invite-link)
│   ├── messages.py      ─ /api/chats/{id}/messages, /api/messages/{id}/* (send, edit, delete, pin, reactions, forward)
│   ├── invites.py       ─ /api/invites/* (create, list, revoke, join)
│   ├── upload.py        ─ /api/upload, /media/{uuid} (multipart, MIME check)
│   ├── scheduled.py     ─ /api/chats/{id}/scheduled (отложенные сообщения)
│   ├── music.py         ─ /api/music/* (1691 строк: tracks, stream, favorites, playlists, lyrics)
│   ├── metrics.py       ─ /api/metrics, /api/admin/dashboard, /internal/metrics
│   └── health.py        ─ /api/health
│
├── notifications/       ─ push-уведомления (outbox pattern)
│   ├── bus.py           ─ EventBus (asyncio.Queue)
│   ├── dispatch.py      ─ consumer: send push to non-muted members
│   ├── sender.py        ─ DevPushSender / WebPushSender (pywebpush + VAPID)
│   ├── router.py        ─ /api/notifications/* (subscribe, unsubscribe, settings)
│   └── vapid.py         ─ VAPID key generation (ECDSA P-256)
│
└── static/              ─ SPA (см. [frontend.md](frontend.md))
    ├── index.html       ─ основной layout + load order для js/ (557 строк)
    ├── admin.html       ─ full-page админка (2133 строки)
    ├── music.html       ─ standalone музыкальный плеер (1292 строки)
    ├── calendar.html    ─ календарь FEAT-11 (месяц/неделя/день + sidebar,
    │                      роут GET /calendar; см. [frontend.md](frontend.md))
    ├── settings.html    ─ настройки пользователя
    ├── reset-password.html ─ сброс пароля по токену
    ├── manifest.json    ─ PWA manifest
    ├── css/app.css      ─ основной CSS (+ css/calendar.css для календаря)
    └── js/              ─ 25 файлов (load order = контракт; см.
                       [frontend.md](frontend.md))
```

**Принцип:** routes — тонкие, бизнес-логика по возможности inline (для
простоты), сложные куски выносятся в helpers (например,
`format_message`, `require_member` в messages.py).

## Что есть только в WS (нет REST-аналога)

| Событие | Триггер | Получатели |
|---|---|---|
| `online` / `offline` | WS first-connect / last-disconnect (multi-socket, BUG-WS-01) | Все контакты из общих чатов |
| `typing` | Клиент шлёт `{type:"typing"}` | Остальные участники чата |
| `pong` | Клиент шлёт `{type:"ping"}` | Только отправитель |
| `read` ack | Клиент шлёт `{type:"read"}` | Только отправитель (но есть REST `/readers`) |

## Что есть только в REST (нет WS-аналога)

- Регистрация/логин (токен выдаётся, не событие)
- Загрузка файлов (multipart, не WS-friendly)
- Поиск пользователей, список чатов, настройки
- Все CRUD на сообщениях (команды, не уведомления)

## Жизненный цикл запроса

### REST
1. `nginx` (или uvicorn напрямую) принимает HTTP
2. `CORSMiddleware` проверяет origin
3. `log_requests` middleware логирует `->`
4. FastAPI резолвит Depends (`get_db`, `get_current_user`)
5. Handler выполняется
6. `get_db` коммитит/роллбэчит
7. `log_requests` логирует `<-`
8. `ExceptionHandler(500)` если что-то пошло не так

### WebSocket
1. `nginx` проксирует Upgrade
2. `websocket_endpoint` (`app/ws.py:200`)
3. Проверка JWT (из query `?token=...`)
4. Проверка `is_blocked`
5. Обновление `last_seen_at`
6. `manager.connect()` — регистрация в `ConnectionManager`
7. `_notify_online_status(online=True)` — broadcast контактам (только
   при первом живом сокете пользователя)
8. Цикл: `receive_text` (с 35s timeout) → dispatch по `msg.type`
9. При disconnect (или 4000 timeout): `manager.disconnect()` + `last_seen_at`
   + `_notify_online_status(online=False)` — только если живых сокетов
   пользователя не осталось (BUG-WS-01)

## Поток сообщения: отправка

```
Client A (WS)               REST                DB            WS broadcast
     │                       │                  │                  │
     │                       │  POST /api/chats/{id}/messages     │
     │                       │─────────────────►│                  │
     │                       │  1. require_member (chat_id, user)  │
     │                       │  2. validate (ALLOWED_MSG_TYPES,    │
     │                       │     file_url prefix)                │
     │                       │  3. create Message (id = ts+chat+usr)│
     │                       │  4. db.commit()                     │
     │                       │  5. manager.broadcast_to_chat(      │
     │                       │       chat_id,                      │
     │                       │       {type:"new_message", data:...},│
     │                       │       member_ids)                   │
     │                       │──────────────────────────────────► │
     │                       │                  │                  │
     │                       │                  │  ─── new_message───► Client B
     │                       │                  │  ─── new_message───► Client C
     │                       │                  │
     │  HTTP 201 + formatted │◄─────────────────│                  │
     │◄──────────────────────│                  │                  │
```

Обрати внимание: WS-broadcast происходит ВНУТРИ HTTP-ответа. Если broadcast
упадёт с ошибкой (мёртвый сокет), она будет поймана `send_to_user` и
сокет будет выкинут из реестра, но HTTP-ответ уже отдан успешно.

## Что вынесено в хелперы (cross-cutting)

| Хелпер | Где | Назначение |
|---|---|---|
| `require_member(chat_id, user_id, db)` | messages.py | Проверка членства в чате (403 если нет) |
| `format_message(msg, ...)` | messages.py | Message → JSON для UI (развёрнутые sender/reactions/reply) |
| `format_chat(chat, ...)` | chats.py | Chat → JSON для UI (развёрнутые members) |
| `_bearer = HTTPBearer()` | каждый routes-файл | FastAPI-схема для OpenAPI (security) |
