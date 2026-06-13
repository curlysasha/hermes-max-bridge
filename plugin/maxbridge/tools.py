"""
maxbridge — тулы и хук пути ответа (сторона Гермеса).

Живёт в venv Гермеса, pymax НЕ импортирует. Вся работа с MAX — через
localhost-control sidecar'а (maxbridge/bridge.py): GET /pending, POST /send.
Хендлеры синхронные, всегда возвращают JSON-строку, никогда не кидают.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

# HOME/plugins/maxbridge/tools.py → parents[2] = HOME
_HERMES_HOME = Path(__file__).resolve().parents[2]
_SIDECAR_CONFIG = _HERMES_HOME / "maxbridge" / "config.json"


def _base_url() -> str:
    host, port = "127.0.0.1", 8787
    try:
        cfg = json.loads(_SIDECAR_CONFIG.read_text(encoding="utf-8"))
        host = cfg.get("control_host", host)
        port = cfg.get("control_port", port)
    except Exception:  # noqa: BLE001
        pass
    return f"http://{host}:{port}"


def _get(path: str, timeout: float = 5.0):
    with urllib.request.urlopen(_base_url() + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path: str, payload: dict, timeout: float = 20.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _base_url() + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _pending_items() -> list[dict]:
    pend = _get("/pending")
    if isinstance(pend, dict):
        return list(pend.values())
    return pend or []


# ------------------------------------ тулы -----------------------------------
MAX_PENDING_SCHEMA = {
    "name": "max_pending",
    "description": (
        "Показать MAX-чаты, в которых пришли сообщения и которые ждут твоего "
        "ответа. Используй, чтобы понять, о каком чате речь, прежде чем отвечать."
    ),
    "parameters": {"type": "object", "properties": {}},
}

MAX_REPLY_SCHEMA = {
    "name": "max_reply",
    "description": (
        "Отправить сообщение в чат MAX ОТ ИМЕНИ ПОЛЬЗОВАТЕЛЯ. Бери chat_id из "
        "max_pending или из контекста ожидающих чатов. После отправки чат "
        "снимается из ожидания."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "integer",
                "description": "ID чата MAX (число, может быть отрицательным для групп)",
            },
            "text": {"type": "string", "description": "Текст сообщения"},
        },
        "required": ["chat_id", "text"],
    },
}


MAX_CHATS_SCHEMA = {
    "name": "max_chats",
    "description": (
        "Обзор чатов MAX. По умолчанию — только с непрочитанными сообщениями "
        "(сколько новых и от кого). Используй на вопросы вроде «что нового в "
        "максе», «кто писал». Передай unread_only=false, чтобы увидеть все чаты."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "unread_only": {
                "type": "boolean",
                "description": "Только чаты с непрочитанными (по умолчанию true)",
            }
        },
    },
}

MAX_HISTORY_SCHEMA = {
    "name": "max_history",
    "description": (
        "Прочитать последние сообщения конкретного чата MAX по chat_id "
        "(бери chat_id из max_chats или max_pending). Используй, чтобы "
        "посмотреть переписку и ответить осмысленно."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {"type": "integer", "description": "ID чата MAX"},
            "count": {
                "type": "integer",
                "description": "Сколько последних сообщений (по умолчанию 15)",
            },
        },
        "required": ["chat_id"],
    },
}


def max_chats(args: dict, **kwargs) -> str:
    unread_only = args.get("unread_only", True)
    flag = "1" if unread_only else "0"
    try:
        return json.dumps(_get(f"/chats?unread_only={flag}", timeout=20), ensure_ascii=False)
    except urllib.error.URLError as e:
        return json.dumps({"error": f"sidecar MAX недоступен ({e.reason})."})
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"max_chats: {type(e).__name__}: {e}"})


def max_history(args: dict, **kwargs) -> str:
    chat_id = args.get("chat_id")
    if chat_id is None:
        return json.dumps({"error": "нужен chat_id (число)"})
    count = int(args.get("count") or 15)
    try:
        return json.dumps(
            _get(f"/history?chat_id={int(chat_id)}&count={count}", timeout=20),
            ensure_ascii=False,
        )
    except urllib.error.URLError as e:
        return json.dumps({"error": f"sidecar MAX недоступен ({e.reason})."})
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"max_history: {type(e).__name__}: {e}"})


def max_pending(args: dict, **kwargs) -> str:
    try:
        items = _pending_items()
        return json.dumps(
            {"count": len(items), "pending": items}, ensure_ascii=False
        )
    except urllib.error.URLError as e:
        return json.dumps(
            {"error": f"sidecar MAX недоступен ({e.reason}). Запущен ли maxbridge/bridge.py?"}
        )
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"max_pending: {type(e).__name__}: {e}"})


def max_reply(args: dict, **kwargs) -> str:
    chat_id = args.get("chat_id")
    text = (args.get("text") or "").strip()
    if chat_id is None or not text:
        return json.dumps({"error": "нужны chat_id (число) и непустой text"})
    try:
        res = _post("/send", {"chat_id": int(chat_id), "text": text})
        return json.dumps(res, ensure_ascii=False)
    except urllib.error.HTTPError as e:
        try:
            return json.dumps(json.loads(e.read().decode("utf-8")), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return json.dumps({"error": f"send HTTP {e.code}"})
    except urllib.error.URLError as e:
        return json.dumps(
            {"error": f"sidecar MAX недоступен ({e.reason}). Запущен ли maxbridge/bridge.py?"}
        )
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"max_reply: {type(e).__name__}: {e}"})


# ------------------------------- pre_llm_call --------------------------------
def inject_pending(**kwargs) -> dict | None:
    """Если есть MAX-сообщения, ждущие ответа — подмешать их в ход агента."""
    try:
        items = _pending_items()
    except Exception:  # noqa: BLE001
        return None  # sidecar лежит — молча, не ломаем ход
    if not items:
        return None
    lines = []
    for it in items:
        cid = it.get("chat_id")
        label = it.get("label", "?")
        text = (it.get("text") or "").replace("\n", " ")[:100]
        lines.append(f"  - chat_id={cid} · {label}: «{text}»")
    return {
        "context": (
            "MAX-сообщения, ждущие твоего ответа. Если пользователь просит "
            "ответить — используй max_reply(chat_id, text) с нужным chat_id:\n"
            + "\n".join(lines)
        )
    }
