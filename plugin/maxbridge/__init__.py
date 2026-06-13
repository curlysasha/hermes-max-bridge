"""maxbridge plugin — путь ответа в MAX (сторона Гермеса). См. tools.py."""

from __future__ import annotations

import logging

from . import tools

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    ctx.register_tool(
        name="max_pending",
        toolset="maxbridge",
        schema=tools.MAX_PENDING_SCHEMA,
        handler=tools.max_pending,
        description="MAX-чаты, ждущие ответа",
        emoji="📨",
    )
    ctx.register_tool(
        name="max_reply",
        toolset="maxbridge",
        schema=tools.MAX_REPLY_SCHEMA,
        handler=tools.max_reply,
        description="Ответить в MAX от имени пользователя",
        emoji="✉️",
    )
    ctx.register_tool(
        name="max_chats",
        toolset="maxbridge",
        schema=tools.MAX_CHATS_SCHEMA,
        handler=tools.max_chats,
        description="Обзор чатов MAX (непрочитанные / все)",
        emoji="📋",
    )
    ctx.register_tool(
        name="max_history",
        toolset="maxbridge",
        schema=tools.MAX_HISTORY_SCHEMA,
        handler=tools.max_history,
        description="Прочитать историю чата MAX",
        emoji="📖",
    )
    ctx.register_hook("pre_llm_call", tools.inject_pending)
    logger.info(
        "maxbridge plugin registered: max_pending, max_reply, max_chats, "
        "max_history, pre_llm_call"
    )
