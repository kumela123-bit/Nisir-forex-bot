# Nisir Forex Academy Telegram Bot

A Telegram sales and payment-verification bot built with Python and
[`python-telegram-bot`](https://python-telegram-bot.org/).

## User flow

1. `/start` opens the academy menu.
2. A user selects a course, VIP Signal, or private mentorship.
3. The bot shows the configured payment instructions.
4. The user confirms payment and uploads a receipt photo.
5. The receipt is forwarded to the admin with **Approve** and **Reject** buttons.
6. Approved VIP purchases receive a one-time invite link to the configured channel.

Payment records are stored in `payments.db`, so pending reviews survive a bot
restart. The database path can be changed with `PAYMENTS_DB_PATH`.

## Admin dashboard

The configured admin can open **⚙️ Admin Dashboard** from `/start` to view:

- 👥 Users and VIP members
- 💳 Pending payments and payment history
- 📸 Pending and historical receipts
- 📚 Course catalog and students
- 🔐 VIP members, saved signals, and access controls
- 🤝 Mentorship users and requests
- 🔑 Course/VIP access status
- 📢 Confirmation-protected broadcasts to users, VIP members, or course students
- 👤 Additional admins
- 📊 Statistics and revenue
- ⚙️ Payment, price, and bot settings

Admin actions are restricted to the owner admin or admins added through the
dashboard. VIP revocations override previous approved VIP purchases until access
is explicitly granted again or the user completes a new approved purchase.

## Run the bot

Required values are stored as Replit Secrets:

- `TELEGRAM_BOT_TOKEN`
- `NISIR_ADMIN_TELEGRAM_ID`
- `NISIR_VIP_CHANNEL_ID`
- `NISIR_CBE_ACCOUNT_NAME`
- `NISIR_CBE_ACCOUNT_NUMBER`
- `NISIR_ABYSSINIA_ACCOUNT_NUMBER`
- `NISIR_TELEBIRR_NUMBER`

Run directly:

```bash
uv run python bot.py
```

The configured **Telegram Bot** workflow runs the same command continuously.

## Telegram permissions

For VIP invite links to work, add the bot to the VIP channel as an
administrator with permission to invite users. The admin must also start a chat
with the bot before the bot can send payment notifications.

Keep all secrets in Replit Secrets. Do not commit bot tokens, admin identifiers,
channel identifiers, or payout details to source control.