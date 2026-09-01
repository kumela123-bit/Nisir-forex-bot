# Telegram Bot

A starter Telegram bot built with Python and
[`python-telegram-bot`](https://python-telegram-bot.org/).

## Included commands

- `/start` — welcome message
- `/help` — list available commands
- `/echo <text>` — repeat text
- `/about` — describe the bot
- Regular text messages are repeated automatically

## Run the bot

The bot token is already configured as the `TELEGRAM_BOT_TOKEN` Replit Secret.

```bash
uv run python bot.py
```

The configured **Telegram Bot** workflow runs the same command continuously.

## Add your own commands

Add an async handler to `bot.py`, then register it in `build_application()`:

```python
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    await update.message.reply_text("Everything is running.")


application.add_handler(CommandHandler("status", status))
```

Keep secrets in Replit Secrets. Do not commit bot tokens or place them directly
in source code.