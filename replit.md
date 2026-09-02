# Nisir Forex Academy Telegram Bot

A Python Telegram sales bot for academy courses, a Master Class, manual receipt review, and VIP channel access.

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
- Reads all operational and payout configuration from Replit Secrets rather than source code.
- Uses SQLite for payment, user, admin, course catalog, VIP access, and saved-signal records so dashboard data survives process restarts.
- Restricts dashboard and payment approval actions to the owner or configured admins.
- Restricts course catalog management to the configured owner admin.
- Requires explicit confirmation before sending an admin broadcast.
- Generates VIP signals from a selected direction, supported pair, and entry price with fixed pip-based SL/TP rules.
- Includes a purchasable Master Class product in the same receipt-review payment flow.

## Product

The bot presents academy products, guides users through bank-transfer payment,
collects receipt photos, forwards them to an administrator, and issues a
one-time VIP channel invite after approval. The admin dashboard provides
visibility into users, payments, receipts, access, products, broadcasts,
statistics, and settings. Courses are persisted in SQLite; the owner can add
courses through a four-step flow, edit fields, and delete added courses with
confirmation. Existing academy courses are seeded and protected from deletion.
VIP signals are generated automatically for the supported XAUUSD, EURUSD,
GBPUSD, USDJPY, and BTCUSD pairs, saved in SQLite, and posted to the configured
VIP channel only after Telegram accepts the message. Startup verifies that the
channel is reachable and that the bot has administrator/post-message
permissions. A failed post is reported to the admin without claiming success.
Admins can select active signals and post persistent TP1–TP5, Break Even, or
Stop Loss status updates; TP5 and Stop Loss mark a signal closed only after
their channel messages are successfully posted.

## User preferences

No additional preferences specified.

## Gotchas

- The bot cannot start without all required Replit Secrets.
- Only one polling process should use a bot token at a time.
- The bot must be an administrator of the VIP channel to create invite links.
- Payment data is local to the SQLite file and is not shared across deployments.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
