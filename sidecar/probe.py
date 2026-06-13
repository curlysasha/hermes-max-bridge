#!/usr/bin/env python
"""
PyMax probe — проверка, что maxapi-python (2.1.3) РЕАЛЬНО говорит с актуальным MAX
твоим личным аккаунтом: авторизация → профиль → список чатов → история одного чата.

Запускается в ИЗОЛИРОВАННОМ sidecar-venv (maxbridge/.venv), а НЕ в venv Гермеса —
pymax несовместим с пинами Гермеса (aiohttp/qrcode/websockets), поэтому живёт отдельно.

Запускать ИНТЕРАКТИВНО (нужно ввести SMS-код, при 2FA — пароль):

    ! maxbridge/.venv/Scripts/python.exe maxbridge/probe.py +7XXXXXXXXXX

Телефон можно не передавать аргументом — тогда спросит. Сессия сохраняется в
maxbridge/session.db, повторный запуск уже не попросит код.
"""

import asyncio
import os
import sys
from pathlib import Path

from pymax import Client

WORKDIR = Path(__file__).resolve().parent
SESSION = "session.db"


def _shorten(text: str | None, n: int = 70) -> str:
    t = (text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


async def main() -> int:
    phone = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("MAX_PHONE")
        or input("Телефон (+7XXXXXXXXXX): ").strip()
    )
    if not phone:
        print("Нет телефона — выхожу.")
        return 2

    print(f"work_dir={WORKDIR}  session={SESSION}")
    client = Client(phone=phone, session_name=SESSION, work_dir=str(WORKDIR))

    @client.on_start()
    async def _ready(c: Client) -> None:
        try:
            me = c.me
            print("\n=== AUTH OK ===")
            print("me:", me.model_dump() if me else None)

            chats = await c.fetch_chats()
            print(f"\n=== ЧАТЫ: {len(chats)} (показываю до 15) ===")
            for ch in chats[:15]:
                kind = (
                    "dialog" if ch.is_dialog
                    else "group" if ch.is_group
                    else "channel" if ch.is_channel
                    else str(ch.type)
                )
                print(
                    f"  id={ch.id:<14} {kind:<8} new={ch.new_messages or 0:<4} "
                    f"{_shorten(ch.title, 40)!r}"
                )

            dialog = next((ch for ch in chats if ch.is_dialog), None) or (
                chats[0] if chats else None
            )
            if dialog is not None:
                print(
                    f"\n=== ИСТОРИЯ чата id={dialog.id} "
                    f"{_shorten(dialog.title, 30)!r} (последние ~8) ==="
                )
                hist = await c.fetch_history(chat_id=dialog.id, backward=8)
                for m in hist or []:
                    print(f"  [{m.time}] sender={m.sender}: {_shorten(m.text)!r}")
            print("\n=== PROBE OK: либа говорит с актуальным MAX ===")
        except Exception as exc:  # noqa: BLE001
            print(f"\n!!! PROBE FAILED во время чтения: {type(exc).__name__}: {exc}")
        finally:
            await c.stop()

    try:
        await client.start()
    except asyncio.CancelledError:
        # Ожидаемо: stop() из on_start гасит recv-loop, который ждал start().
        # Это штатное завершение probe, а не ошибка.
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"\n!!! PROBE FAILED на авторизации/коннекте: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
