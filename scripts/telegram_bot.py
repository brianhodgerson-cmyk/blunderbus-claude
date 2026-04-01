#!/usr/bin/env python3
"""
BlunderBus Telegram Bot.

Bridges Telegram messages to Claude Code from the repo root so CLAUDE.md loads.
Conversation history can live in memory or Redis, and each interaction is logged
to the life_events table when ClickHouse is available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

from blunderbus_data import log_life_event
from chat_history import create_history_store
from runtime import configure_utf8_stdio, project_root, resolve_claude_command

configure_utf8_stdio()

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

CLAUDE_CMD = resolve_claude_command()
PROJECT_DIR = str(project_root())
CLAUDE_TIMEOUT = 180
MAX_HISTORY_TURNS = 8
HISTORY_STORE = create_history_store(MAX_HISTORY_TURNS)


def _allowed_ids() -> set[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}


def _build_prompt(history, message: str) -> str:
    if not history:
        return message
    lines = ["Prior conversation (for context):\n"]
    for role, text in history:
        lines.append(f"{role}: {text}")
    lines.append(f"\nUser: {message}")
    return "\n".join(lines)


def _call_claude(prompt: str) -> str:
    if not CLAUDE_CMD:
        return "Claude CLI not found. Set CLAUDE_BIN or install Claude Code."

    try:
        result = subprocess.run(
            [CLAUDE_CMD, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_TIMEOUT,
            cwd=PROJECT_DIR,
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            err = result.stderr.strip()[:300]
            logger.error("Claude exited %s: %s", result.returncode, err)
            return f"Claude returned an error (exit {result.returncode})."
        return output or "No response."
    except subprocess.TimeoutExpired:
        return f"Request timed out after {CLAUDE_TIMEOUT}s. Try a simpler query."
    except FileNotFoundError:
        return f"Claude CLI not found at {CLAUDE_CMD}. Is Claude Code installed?"
    except Exception as exc:
        logger.exception("Unexpected error calling Claude")
        return f"Unexpected error: {exc}"


def _log_interaction(chat_id: int, user, prompt: str, response: str) -> None:
    log_life_event(
        domain="telegram",
        event_type="query",
        source="telegram",
        summary=prompt[:160],
        detail=json.dumps(
            {
                "chat_id": chat_id,
                "user_id": user.id,
                "username": user.username,
                "prompt": prompt,
                "response_summary": response[:600],
            },
            ensure_ascii=True,
        ),
        tags=["telegram", "assistant"],
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "BlunderBus online.\n\nAsk me anything about HodgeSpot - infra, home systems, finance. "
        "Use /reset to clear conversation history, /help for commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start - wake up BlunderBus\n"
        "/reset - clear conversation history\n"
        "/help  - this message\n\n"
        "Just send any message to ask BlunderBus a question.\n"
        "Use `log: <text>` to append a manual life-log entry."
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    HISTORY_STORE.clear(update.effective_chat.id)
    await update.message.reply_text("Conversation history cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    allowed = _allowed_ids()

    if allowed and user.id not in allowed:
        logger.warning("Rejected message from user_id=%s (@%s)", user.id, user.username)
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if text.lower().startswith("log:"):
        entry = text[4:].strip()
        if not entry:
            await update.message.reply_text("Usage: log: <what happened>")
            return
        log_life_event(
            domain="log",
            event_type="manual_log",
            source="telegram",
            summary=entry[:160],
            detail={
                "chat_id": chat_id,
                "user_id": user.id,
                "username": user.username,
                "entry": entry,
            },
            tags=["telegram", "manual-log"],
        )
        await update.message.reply_text(f"Logged: {entry}")
        return

    prompt = _build_prompt(HISTORY_STORE.get(chat_id), text)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _call_claude, prompt)

    HISTORY_STORE.append(chat_id, "User", text)
    HISTORY_STORE.append(chat_id, "Assistant", response[:600])
    _log_interaction(chat_id, user, text, response)

    if len(response) <= 4096:
        await update.message.reply_text(response)
        return

    for i in range(0, len(response), 4096):
        await update.message.reply_text(response[i : i + 4096])


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Load via run_telegram_bot.ps1.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    allowed = _allowed_ids()
    if allowed:
        logger.info("Allowlist active - %s user ID(s) permitted", len(allowed))
    else:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS not set - bot will respond to ANYONE")

    logger.info("BlunderBus Telegram bot starting (long-poll)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
