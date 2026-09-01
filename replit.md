# Telegram Bot

A Python Telegram bot that responds to commands and repeats regular text messages.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `uv run python bot.py` — run the Telegram bot locally
- `telegram-bot` workflow — run the bot continuously
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required secret: `TELEGRAM_BOT_TOKEN` — Telegram bot token from BotFather

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `bot.py` — bot setup, command handlers, message handling, and error logging
- `README.md` — setup, run, and extension instructions
- `pyproject.toml` — Python dependency and script configuration

## Architecture decisions

- Uses async handlers and long polling from `python-telegram-bot`.
- Reads the Telegram token from Replit Secrets rather than source code.
- Keeps the initial command surface intentionally small so application-specific behavior can be added in `bot.py`.

## Product

The bot welcomes users, explains its commands, echoes requested text, and responds to ordinary text messages.

## User preferences

No additional preferences specified.

## Gotchas

- The bot cannot start without `TELEGRAM_BOT_TOKEN`.
- Only one polling process should use a bot token at a time.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
