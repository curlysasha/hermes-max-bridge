# hermes-max-bridge

Мост между мессенджером **MAX** и **[Hermes Agent](https://github.com/NousResearch/hermes)**:
агент мгновенно присылает тебе в Telegram новые личные сообщения из MAX и по твоей
просьбе отвечает на них от твоего имени, а также умеет показать непрочитанное и
прочитать историю любого чата.

Транспорт к MAX — [`maxapi-python`](https://github.com/MaxApiTeam/PyMax) (PyMax,
неофициальный внутренний API, userbot-режим: логинится как ты и видит всю личную
переписку).

---

## Как это устроено

Два независимых пути. Вход — дешёвый детерминированный форвард (без LLM, мгновенно).
Выход — LLM подключается только когда ты сам решаешь ответить.

```
            ┌──────────────── PyMax (один логин = ты) ────────────────┐
            │                                                          │
   ВХОД (мгновенно, без LLM)                       ВЫХОД (LLM только когда ты вовлечён)
   on_message → резолв имени                Ты в Telegram: «ответь в макс: …»
        │                                          │
   пуш тебе в Telegram (Bot API)            Сессия агента Hermes
   📨 MAX · Имя: текст                       │  тулы: max_pending / max_chats /
        │                                    │        max_history / max_reply
   pending-стор                             POST /send → PyMax шлёт как ты
```

### Почему два процесса (sidecar + плагин), а не один

PyMax требует `aiohttp>=3.13.5`, `qrcode>=8.2`, `websockets>=16.0`, а Hermes **жёстко
пинит** `aiohttp==3.13.4` (security-CVE) и `qrcode==7.4.2`. Они **несовместимы в одном
venv**. Поэтому:

- **sidecar** (`sidecar/bridge.py`) живёт в собственном изолированном venv с PyMax,
  отдельным процессом. Держит постоянный WS к MAX, пушит входящие в Telegram, отдаёт
  локальный HTTP-control (`/health`, `/pending`, `/chats`, `/history`, `/send`).
- **плагин Hermes** (`plugin/maxbridge/`) живёт в venv Hermes, PyMax **не импортирует** —
  только ходит на localhost-control sidecar'а. Даёт агенту тулы и хук `pre_llm_call`.
- **watchdog** (`scripts/maxbridge-watchdog.py`) — cron-задача Hermes (`--no-agent`,
  раз в минуту): проверяет `/health` и поднимает sidecar, если тот лёг (и после краша).

Итог: Hermes владеет всем жизненным циклом, его venv остаётся чистым, рискованный
неофициальный API изолирован.

---

## Компоненты

| Путь | Где исполняется | Что делает |
|---|---|---|
| `sidecar/bridge.py` | свой venv (PyMax) | постоянный listener MAX + пуш в Telegram + control-HTTP |
| `sidecar/probe.py` | свой venv (PyMax) | разовая проверка: логин + чтение чатов/истории |
| `plugin/maxbridge/` | venv Hermes | тулы агента + `pre_llm_call`-хук (без PyMax) |
| `scripts/maxbridge-watchdog.py` | python Hermes | cron-watchdog, поднимает/сторожит sidecar |
| `install/make-startup-shortcuts.ps1` | — | ярлыки автозагрузки (desktop Hermes, локальная модель) |

### Тулы, которые получает агент

| Тул | Назначение |
|---|---|
| `max_pending` | чаты, ждущие ответа |
| `max_reply(chat_id, text)` | ответить в MAX от твоего имени |
| `max_chats(unread_only?)` | обзор непрочитанного / всех чатов |
| `max_history(chat_id, count?)` | прочитать последние сообщения чата |

Плюс хук `pre_llm_call` подмешивает в ход агента список MAX-чатов, ждущих ответа.

---

## Установка

Предполагается рабочая установка Hermes с домашней папкой `~/.hermes`
(на Windows обычно `C:\Users\<user>\AppData\Local\hermes`).

### 1. Sidecar (изолированный venv)

```cmd
cd %LOCALAPPDATA%\hermes
mkdir maxbridge
copy <repo>\sidecar\bridge.py        maxbridge\
copy <repo>\sidecar\probe.py         maxbridge\
copy <repo>\sidecar\config.example.json maxbridge\config.json

py -3.11 -m venv maxbridge\.venv
maxbridge\.venv\Scripts\python -m pip install -r <repo>\sidecar\requirements.txt
```

Открой `maxbridge\config.json`, впиши свой `phone` (формат `+7…`).

### 2. Probe — убедиться, что либа говорит с актуальным MAX

```cmd
maxbridge\.venv\Scripts\python maxbridge\probe.py +7XXXXXXXXXX
```

Введёшь SMS-код (при 2FA — пароль). Сессия сохранится в `maxbridge\session.db`,
повторный код больше не нужен. Должен вывести профиль, чаты и историю →
`PROBE OK`.

### 3. Плагин Hermes

```cmd
mkdir %LOCALAPPDATA%\hermes\plugins\maxbridge
copy <repo>\plugin\maxbridge\* %LOCALAPPDATA%\hermes\plugins\maxbridge\
```

Включи плагин в `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - maxbridge
```

Sidecar пушит тебе через Telegram-бота Hermes — нужны заданные в `~/.hermes/.env`
`TELEGRAM_BOT_TOKEN` и `TELEGRAM_HOME_CHANNEL` (или укажи `owner_telegram_chat_id`
в `config.json`).

### 4. Watchdog (cron, автозапуск + супервизия sidecar)

```cmd
copy <repo>\scripts\maxbridge-watchdog.py %LOCALAPPDATA%\hermes\scripts\
hermes cron create "every 1m" --no-agent --script maxbridge-watchdog.py --deliver local --name maxbridge-watchdog
```

### 5. Перезапуск gateway (подхватить плагин)

```cmd
hermes gateway restart
```

После этого: входящие из MAX → Telegram; «ответь в макс: …» → уходит в MAX;
«что нового в максе» → `max_chats`; «покажи переписку с X» → `max_history`.

---

## Конфиг (`sidecar/config.json`)

| Поле | Смысл |
|---|---|
| `phone` | твой номер (`+7…`) |
| `owner_telegram_chat_id` | куда слать пуши; пусто = `TELEGRAM_HOME_CHANNEL` из `.env` |
| `control_host` / `control_port` | адрес локального control-сервера (по умолчанию `127.0.0.1:8787`) |
| `ignore_senders` | id отправителей, которых не уведомлять (по умолчанию системный аккаунт MAX `543835`) |
| `notify_groups` | `false` = только личные диалоги; `true` = и группы/каналы |

---

## Автозапуск (Windows)

`install/make-startup-shortcuts.ps1` создаёт ярлыки в папке `Startup` для
desktop-приложения Hermes и локального сервера модели. Сам sidecar поднимает
cron-watchdog внутри Hermes — отдельный автозапуск ему не нужен.

Полная цепочка после входа в Windows: gateway (login item) → плагин → cron →
sidecar (по сохранённой сессии) → слушает MAX.

---

## Безопасность

- **`session.db` = токен полного доступа к твоему аккаунту MAX.** Никогда не коммить,
  не пересылай. В `.gitignore`.
- **`config.json` содержит телефон** — в репо только `config.example.json`.
- Telegram-бот пускает только allowlist/спаренных (`TELEGRAM_ALLOWED_USERS` / DM-pairing
  в Hermes). Поставь свою личку home-каналом (`/sethome`).
- control-сервер слушает только `127.0.0.1`.

## Нюансы неофициального API

- Протокол MAX меняется без предупреждения → версия `maxapi-python` **запинена**.
- Возможны риски для аккаунта (ToS). По возможности — на непервостепенном аккаунте.
- Если MAX когда-нибудь сбросит сессию — разово прогнать `probe.py` и ввести новый код.
- Один процесс sidecar = одна MAX-сессия. Двух одновременно держать нельзя (роняет связь);
  watchdog гарантирует ровно один экземпляр через проверку `/health`.

---

## Лицензия

Не задана. Добавь по необходимости (например, MIT/Apache-2.0).
