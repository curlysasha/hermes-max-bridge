#!/usr/bin/env python
"""
Watchdog MAX-моста для Гермес-крона (--no-agent).

Тикает раз в минуту ВНУТРИ Гермеса. Если control-сервер sidecar'а отвечает —
процесс жив, ничего не делаем (тихий тик). Если не отвечает — sidecar лежит
(не запускался / упал), подымаем его detached-процессом в его же venv.

Запускается python'ом Гермеса (sys.executable), поэтому pymax НЕ импортирует —
только urllib + subprocess. Сам мост о подключении сообщит своим «✅ на связи»,
так что watchdog молчит (пустой stdout = тихий тик, ничего не доставляется).
"""

import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]          # scripts/ -> HERMES_HOME
MB = HOME / "maxbridge"
CONFIG = MB / "config.json"
# pythonw.exe — безоконный интерпретатор: фоновый процесс без пустого консольного окна.
_PYW = MB / ".venv" / "Scripts" / "pythonw.exe"
_PY = MB / ".venv" / "Scripts" / "python.exe"
SIDECAR_PY = _PYW if _PYW.exists() else _PY
BRIDGE = MB / "bridge.py"
WD_LOG = MB / "bridge-watchdog.log"


def control_port() -> int:
    try:
        return int(json.loads(CONFIG.read_text(encoding="utf-8")).get("control_port", 8787))
    except Exception:
        return 8787


def process_alive() -> bool:
    """True, если control-сервер sidecar'а откликается (значит процесс жив)."""
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{control_port()}/health", timeout=4
        )
        return True
    except Exception:
        return False


def log(msg: str) -> None:
    try:
        with WD_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def launch_sidecar() -> None:
    if not SIDECAR_PY.exists() or not BRIDGE.exists():
        log(f"НЕ могу запустить: нет {SIDECAR_PY if not SIDECAR_PY.exists() else BRIDGE}")
        return
    # Windows: фоновый процесс БЕЗ окна, переживающий тик крона.
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    flags = 0
    if sys.platform == "win32":
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    out = WD_LOG.open("a", encoding="utf-8")
    subprocess.Popen(
        [str(SIDECAR_PY), str(BRIDGE)],
        cwd=str(HOME),
        stdout=out,
        stderr=out,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    log("sidecar лежал → запустил detached")


def main() -> int:
    if process_alive():
        return 0          # жив — тихо
    launch_sidecar()
    return 0              # пустой stdout = тихий тик


if __name__ == "__main__":
    raise SystemExit(main())
