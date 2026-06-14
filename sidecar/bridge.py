#!/usr/bin/env python
"""
maxbridge sidecar — постоянный listener личного MAX через PyMax.

Живёт в ИЗОЛИРОВАННОМ venv (maxbridge/.venv), отдельным процессом от Гермеса:
pymax несовместим с пинами Гермеса (aiohttp/qrcode/websockets), поэтому в его venv
его быть не должно. Связь с Гермесом — через localhost-HTTP (см. control-сервер).

Две функции:
  1. ВХОД (мгновенно, без LLM): on_message → резолв имени → пуш тебе в Telegram
     (Bot API REST) + запись в pending.json. Это твой приоритет «присылать сразу».
  2. ВЫХОД (по твоей просьбе): Гермес-плагин дергает POST /send на этом сервере →
     одно и то же MAX-соединение отправляет сообщение от твоего имени. Одна сессия,
     никаких вторых логинов.

Запуск (в sidecar-venv):
    maxbridge\.venv\Scripts\python.exe maxbridge\bridge.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import aiohttp
from aiohttp import web

from pymax import Client, ExtraConfig

HERE = Path(__file__).resolve().parent
HERMES_HOME = HERE.parent
CONFIG_PATH = HERE / "config.json"
PENDING_PATH = HERE / "pending.json"
ENV_PATH = HERMES_HOME / ".env"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [bridge] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("maxbridge")


# ----------------------------- конфиг / окружение ----------------------------
def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.setdefault("control_host", "127.0.0.1")
    cfg.setdefault("control_port", 8765)
    cfg.setdefault("ignore_senders", [])
    cfg.setdefault("notify_groups", False)
    return cfg


def read_hermes_env(keys: set[str]) -> dict[str, str]:
    """Тонко вытаскиваем нужные значения из .env Гермеса (только чтение)."""
    out: dict[str, str] = {}
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in keys:
                out[k] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        log.warning("нет %s — Telegram-пуш не настроится", ENV_PATH)
    return out


# ----------------------------- pending-хранилище -----------------------------
def load_pending() -> dict:
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_pending(data: dict) -> None:
    PENDING_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _attach_type(a) -> str:
    """Тип вложения строкой в верхнем регистре: PHOTO / CONTROL / VIDEO / ..."""
    t = getattr(a, "type", None)
    return str(getattr(t, "value", t) or "").upper()


# --------------------------------- сам мост ----------------------------------
class Bridge:
    def __init__(self, cfg: dict, env: dict[str, str]) -> None:
        self.cfg = cfg
        self.tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
        self.owner_chat = (
            str(cfg.get("owner_telegram_chat_id") or "").strip()
            or env.get("TELEGRAM_HOME_CHANNEL", "").strip()
        )
        self.ignore = set(cfg.get("ignore_senders", []))
        self.notify_groups = bool(cfg.get("notify_groups", False))
        self.my_id: int | None = None
        self._announced = False  # пинг «на связи» только при первом коннекте
        self._chats: dict[int, object] = {}  # chat_id -> Chat
        self._name_cache: dict[int, str] = {}  # uid -> имя (чтобы не дёргать get_user повторно)
        self.client = Client(
            phone=cfg["phone"],
            session_name=cfg.get("session_name", "session.db"),
            work_dir=str(HERE),
            extra_config=ExtraConfig(reconnect=True, reconnect_delay=3.0),
        )
        self._http: aiohttp.ClientSession | None = None

    # --- утилиты резолва имён/чатов ---
    async def sender_name(self, uid: int | None) -> str:
        if not uid:
            return "?"
        if uid in self._name_cache:
            return self._name_cache[uid]
        try:
            user = self.client.get_cached_user(uid) or await self.client.get_user(uid)
        except Exception:  # noqa: BLE001
            user = None
        label = None
        names = getattr(user, "names", None) if user else None
        if names:
            n = names[0]
            # В живом API это pydantic-объект Name, не словарь — берём через атрибуты.
            if isinstance(n, dict):
                label = n.get("name") or n.get("first_name")
            else:
                label = getattr(n, "name", None) or getattr(n, "first_name", None)
        result = str(label) if label else str(uid)
        if label:  # кэшируем только удачный резолв
            self._name_cache[uid] = result
        return result

    def chat_meta(self, chat_id: int):
        return self._chats.get(chat_id)

    async def chat_label(self, chat) -> str:
        """Человекочитаемое имя чата: title для групп, собеседник для диалога."""
        if chat is None:
            return "?"
        if getattr(chat, "is_group", False) or getattr(chat, "is_channel", False):
            return chat.title or str(chat.id)
        # диалог: имя собеседника = участник, отличный от меня
        parts = getattr(chat, "participants", None)
        pid = None
        if isinstance(parts, dict):
            for k in parts:
                try:
                    if int(k) != self.my_id:
                        pid = int(k)
                        break
                except (TypeError, ValueError):
                    continue
        elif isinstance(parts, (list, tuple)):
            for p in parts:
                v = p if isinstance(p, int) else (
                    p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
                )
                if v and int(v) != self.my_id:
                    pid = int(v)
                    break
        if pid:
            return await self.sender_name(pid)
        return chat.title or str(chat.id)

    async def refresh_chats(self) -> None:
        try:
            chats = await self.client.fetch_chats()
            self._chats = {c.id: c for c in chats}
            log.info("чатов в кэше: %d", len(self._chats))
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh_chats: %s", exc)

    # --- Telegram push ---
    async def push_telegram(self, text: str) -> None:
        if not (self.tg_token and self.owner_chat):
            log.error("нет TELEGRAM_BOT_TOKEN/owner_chat — не могу пушить: %s", text[:60])
            return
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        try:
            assert self._http is not None
            async with self._http.post(
                url, json={"chat_id": self.owner_chat, "text": text}
            ) as r:
                if r.status != 200:
                    log.error("telegram %s: %s", r.status, (await r.text())[:200])
        except Exception as exc:  # noqa: BLE001
            log.error("telegram push failed: %s", exc)

    async def push_telegram_photo(self, photo_url: str, caption: str = "") -> bool:
        """Отправить фото в Telegram: сперва ссылкой (Telegram заберёт сам),
        при неудаче — скачать байты (sidecar авторизован в MAX) и залить."""
        if not (self.tg_token and self.owner_chat):
            log.error("нет TELEGRAM_BOT_TOKEN/owner — фото не отправить")
            return False
        api = f"https://api.telegram.org/bot{self.tg_token}/sendPhoto"
        assert self._http is not None
        # 1) отдать ссылку
        try:
            payload = {"chat_id": self.owner_chat, "photo": photo_url}
            if caption:
                payload["caption"] = caption[:1024]
            async with self._http.post(api, json=payload) as r:
                if r.status == 200:
                    return True
                log.warning(
                    "sendPhoto by URL не прошёл (%s: %s) — качаю и заливаю",
                    r.status, (await r.text())[:200],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("sendPhoto by URL ошибка (%s) — качаю и заливаю", exc)
        # 2) фолбэк: скачать и залить multipart
        try:
            async with self._http.get(photo_url) as ir:
                if ir.status != 200:
                    log.error("не скачать фото из MAX (%s)", ir.status)
                    return False
                data = await ir.read()
            form = aiohttp.FormData()
            form.add_field("chat_id", str(self.owner_chat))
            if caption:
                form.add_field("caption", caption[:1024])
            form.add_field("photo", data, filename="photo.jpg", content_type="image/jpeg")
            async with self._http.post(api, data=form) as r2:
                if r2.status != 200:
                    log.error("sendPhoto upload не прошёл (%s: %s)", r2.status, (await r2.text())[:200])
                    return False
                return True
        except Exception as exc:  # noqa: BLE001
            log.error("sendPhoto fallback ошибка: %s", exc)
            return False

    # --- обработка входящего ---
    # PyMax-диспетчер зовёт callback(event, client) → принимаем оба аргумента.
    async def on_message(self, msg, client=None) -> None:
        try:
            if msg.sender and self.my_id and msg.sender == self.my_id:
                return  # своё сообщение
            if msg.sender in self.ignore:
                return

            chat = self.chat_meta(msg.chat_id)
            is_group = bool(chat and (chat.is_group or chat.is_channel))
            if is_group and not self.notify_groups:
                return

            attaches = list(getattr(msg, "attaches", None) or [])
            types = [_attach_type(a) for a in attaches]

            # Служебные события (контакт присоединился/вышел и т.п.) — не пушим.
            if "CONTROL" in types:
                return

            who = await self.sender_name(msg.sender)
            if is_group and chat is not None:
                label = f"{who} в «{chat.title or msg.chat_id}»"
            else:
                label = who
            text = (msg.text or "").strip()
            photos = [a for a in attaches if _attach_type(a) == "PHOTO"]

            if photos:
                caption = f"📨 MAX · {label}" + (f":\n{text}" if text else "")
                for i, ph in enumerate(photos):
                    url = getattr(ph, "base_url", None)
                    if url:
                        await self.push_telegram_photo(url, caption if i == 0 else "")
                pend_text = text or "[фото]"
                log.info("→ пуш(фото×%d): %s: %s", len(photos), label, (text or "")[:50])
            elif text:
                await self.push_telegram(f"📨 MAX · {label}:\n{text}")
                pend_text = text
                log.info("→ пуш: %s: %s", label, text[:60])
            else:
                kind = types[0].lower() if types else "вложение"
                await self.push_telegram(f"📨 MAX · {label}: [{kind}]")
                pend_text = f"[{kind}]"
                log.info("→ пуш(%s): %s", kind, label)

            pend = load_pending()
            pend[str(msg.chat_id)] = {
                "chat_id": msg.chat_id,
                "label": label,
                "from": who,
                "from_id": msg.sender,
                "text": pend_text[:500],
                "time": msg.time,
            }
            save_pending(pend)
        except Exception as exc:  # noqa: BLE001
            log.exception("on_message error: %s", exc)

    # --- control-сервер (мост из Гермеса) ---
    async def h_health(self, _req):
        return web.json_response(
            {"ok": True, "connected": self.my_id is not None, "my_id": self.my_id}
        )

    async def h_pending(self, _req):
        return web.json_response(load_pending())

    async def h_send(self, req):
        try:
            data = await req.json()
            chat_id = int(data["chat_id"])
            text = str(data["text"])
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"error": f"bad request: {exc}"}, status=400)
        try:
            sent = await self.client.send_message(chat_id=chat_id, text=text)
            pend = load_pending()
            pend.pop(str(chat_id), None)  # ответили — снимаем из ожидания
            save_pending(pend)
            mid = getattr(sent, "id", None)
            log.info("← отправлено в %s: %s", chat_id, text[:60])
            return web.json_response({"ok": True, "message_id": mid})
        except Exception as exc:  # noqa: BLE001
            log.error("send failed: %s", exc)
            return web.json_response({"error": str(exc)}, status=500)

    async def h_chats(self, req):
        unread_only = req.query.get("unread_only", "1") not in ("0", "false", "no")
        try:
            chats = await self.client.fetch_chats()
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"error": str(exc)}, status=500)
        wanted = [
            c for c in chats
            if not unread_only or (getattr(c, "new_messages", 0) or 0)
        ]
        # Имена диалогов резолвим ПАРАЛЛЕЛЬНО (иначе 50 чатов = 50 запросов подряд).
        labels = await asyncio.gather(
            *(self.chat_label(c) for c in wanted), return_exceptions=True
        )
        out = []
        for c, label in zip(wanted, labels):
            kind = (
                "group" if getattr(c, "is_group", False)
                else "channel" if getattr(c, "is_channel", False)
                else "dialog"
            )
            out.append(
                {
                    "chat_id": c.id,
                    "unread": getattr(c, "new_messages", 0) or 0,
                    "label": str(label) if not isinstance(label, BaseException) else str(c.id),
                    "type": kind,
                }
            )
        out.sort(key=lambda x: -x["unread"])
        return web.json_response({"count": len(out), "chats": out})

    async def h_history(self, req):
        try:
            chat_id = int(req.query["chat_id"])
            count = int(req.query.get("count", "15"))
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"error": f"bad params: {exc}"}, status=400)
        try:
            hist = await self.client.fetch_history(chat_id=chat_id, backward=count) or []
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"error": str(exc)}, status=500)
        cache: dict[int, str] = {}
        msgs = []
        for m in hist:
            sid = m.sender
            if sid not in cache:
                cache[sid] = "Вы" if sid == self.my_id else await self.sender_name(sid)
            msgs.append({"from": cache[sid], "text": m.text or "", "time": m.time})
        return web.json_response({"chat_id": chat_id, "count": len(msgs), "messages": msgs})

    async def run_control(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self.h_health)
        app.router.add_get("/pending", self.h_pending)
        app.router.add_post("/send", self.h_send)
        app.router.add_get("/chats", self.h_chats)
        app.router.add_get("/history", self.h_history)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.cfg["control_host"], self.cfg["control_port"])
        await site.start()
        log.info(
            "control-сервер: http://%s:%s (/health /pending /send)",
            self.cfg["control_host"],
            self.cfg["control_port"],
        )

    async def run(self) -> None:
        self._http = aiohttp.ClientSession()

        @self.client.on_start()
        async def _ready(c: Client) -> None:  # noqa: ANN202
            me = c.me
            self.my_id = me.contact.id if me and me.contact else None
            log.info("MAX подключён, my_id=%s", self.my_id)
            await self.refresh_chats()
            if not self._announced:
                self._announced = True
                if self.owner_chat and self.tg_token:
                    await self.push_telegram("✅ MAX-мост на связи, слушаю входящие.")
            else:
                log.info("переподключение — пинг в Telegram не дублирую")

        self.client.on_message()(self.on_message)

        await self.run_control()
        try:
            await self.client.start()  # слушает до закрытия, сам реконнектится
        finally:
            if self._http:
                await self._http.close()


async def main() -> int:
    cfg = load_config()
    env = read_hermes_env({"TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"})
    bridge = Bridge(cfg, env)
    log.info(
        "старт. owner_tg=%s  токен=%s  ignore=%s  notify_groups=%s",
        bridge.owner_chat or "(нет)",
        "есть" if bridge.tg_token else "НЕТ",
        sorted(bridge.ignore),
        bridge.notify_groups,
    )
    try:
        await bridge.run()
    except asyncio.CancelledError:
        log.info("остановлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
