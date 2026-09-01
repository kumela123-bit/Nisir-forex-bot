"""A small Telegram bot built with python-telegram-bot."""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOGGER = logging.getLogger(__name__)


def get_bot_token() -> str:
    """Read the bot token and fail with an actionable message if it is missing."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Add your token in Replit Secrets "
            "before starting the bot."
        )
    return token


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome a user and introduce the available commands."""
    del context
    user = update.effective_user
    first_name = user.first_name if user else "there"
    await update.message.reply_text(
        f"Hi {first_name}! I’m your Telegram bot.\n\n"
        "Try /help to see what I can do."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Describe the bot's starter commands."""
    del context
    await update.message.reply_text(
        "Here are the commands I know:\n\n"
        "/start — welcome message\n"
        "/help — show this help message\n"
        "/echo <text> — repeat some text\n"
        "/about — learn about this bot\n\n"
        "You can also send me a regular message and I’ll repeat it."
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repeat the text supplied after /echo."""
    message = update.message
    text = " ".join(context.args).strip()
    if not text:
        await message.reply_text("Usage: /echo <text>\n\nFor example: /echo hello")
        return

    await message.reply_text(text)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Share a short description of the bot."""
    del context
    await update.message.reply_text(
        "I’m a Python Telegram bot powered by python-telegram-bot. "
        "Add your own commands and integrations in bot.py."
    )


async def repeat_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repeat non-command text messages."""
    message = update.message
    await context.bot.send_chat_action(
        chat_id=message.chat_id,
        action=ChatAction.TYPING,
    )
    await message.reply_text(f"You said: {message.text}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unexpected update errors without stopping the polling loop."""
    LOGGER.error("Exception while processing update %r", update, exc_info=context.error)


def build_application() -> Application:
    """Build and configure the Telegram application."""
    application = Application.builder().token(get_bot_token()).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("echo", echo))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, repeat_message)
    )
    application.add_error_handler(error_handler)

    return application


def main() -> None:
    """Start the bot using Telegram's long-polling transport."""
    logging.basicConfig(
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    # HTTP request URLs contain the bot token, so never emit httpx request logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    LOGGER.info("Starting Telegram bot")
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()