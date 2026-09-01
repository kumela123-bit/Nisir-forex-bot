---
name: Telegram bot logging
description: Safe logging guidance for python-telegram-bot services.
---

Telegram API request URLs include the bot token. Keep the HTTP client logger at
warning level or otherwise redact request URLs before enabling application
logging.

**Why:** Verbose HTTP logging can expose the bot credential in workflow logs.

**How to apply:** When configuring logging for a python-telegram-bot process,
avoid request-level `httpx` output in development and production.