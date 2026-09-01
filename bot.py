"""Nisir Forex Academy Telegram bot.

The bot uses inline menus for course sales and manual payment verification.
Operational identifiers and payout details are read from Replit Secrets.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    token: str
    admin_telegram_id: int
    vip_channel_id: int
    cbe_account_name: str
    cbe_account_number: str
    abyssinia_account_number: str
    telebirr_number: str
    database_path: Path

    @classmethod
    def from_env(cls) -> Settings:
        """Load required settings and fail clearly when one is missing."""

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"{name} is not set in Replit Secrets.")
            return value

        def required_int(name: str) -> int:
            value = required(name)
            try:
                return int(value)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be a whole number.") from exc

        return cls(
            token=required("TELEGRAM_BOT_TOKEN"),
            admin_telegram_id=required_int("NISIR_ADMIN_TELEGRAM_ID"),
            vip_channel_id=required_int("NISIR_VIP_CHANNEL_ID"),
            cbe_account_name=required("NISIR_CBE_ACCOUNT_NAME"),
            cbe_account_number=required("NISIR_CBE_ACCOUNT_NUMBER"),
            abyssinia_account_number=required("NISIR_ABYSSINIA_ACCOUNT_NUMBER"),
            telebirr_number=required("NISIR_TELEBIRR_NUMBER"),
            database_path=Path(os.getenv("PAYMENTS_DB_PATH", "payments.db")),
        )


@dataclass(frozen=True)
class Product:
    """A product available for purchase."""

    name: str
    price: str


PRODUCTS: dict[str, Product] = {
    "basic_smc": Product("Basic Smart Money Concept", "$39"),
    "advanced_smc": Product("Advanced SMC Mastery", "$59"),
    "vip": Product("VIP Signal", "$20"),
    "mentorship": Product("Private 1-to-1 Mentorship", "$99"),
}


class PaymentStore:
    """Small SQLite-backed payment store that survives bot restarts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    user_id INTEGER PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    price TEXT NOT NULL,
                    status TEXT NOT NULL,
                    receipt_file_id TEXT,
                    vip_invite_link TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def upsert_pending(self, user_id: int, product_id: str, product: Product) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO payments (
                    user_id, product_id, product_name, price, status,
                    receipt_file_id, vip_invite_link, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    product_id = excluded.product_id,
                    product_name = excluded.product_name,
                    price = excluded.price,
                    status = excluded.status,
                    receipt_file_id = NULL,
                    vip_invite_link = NULL,
                    updated_at = excluded.updated_at
                """,
                (user_id, product_id, product.name, product.price, now, now),
            )

    def get(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_status(self, user_id: int, status: str) -> None:
        self._update(user_id, status=status)

    def save_receipt(self, user_id: int, receipt_file_id: str) -> None:
        self._update(
            user_id,
            status="under_review",
            receipt_file_id=receipt_file_id,
        )

    def save_invite_link(self, user_id: int, invite_link: str) -> None:
        self._update(
            user_id,
            status="approved",
            vip_invite_link=invite_link,
        )

    def _update(self, user_id: int, **values: str) -> None:
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        parameters = [*values.values(), user_id]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE payments SET {assignments} WHERE user_id = ?",
                parameters,
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main_menu(user_id: int, settings: Settings) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📚 Courses", callback_data="courses")],
        [InlineKeyboardButton("🔐 VIP Signal", callback_data="vip_signal")],
        [
            InlineKeyboardButton(
                "🤝 Private 1-to-1 Mentorship",
                callback_data="mentorship",
            )
        ],
        [
            InlineKeyboardButton("👤 My Account", callback_data="my_account"),
            InlineKeyboardButton(
                "💳 Payment Status",
                callback_data="payment_status",
            ),
        ],
    ]
    if user_id == settings.admin_telegram_id:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "⚙️ Admin Dashboard",
                    callback_data="admin_dashboard",
                )
            ]
        )
    return InlineKeyboardMarkup(keyboard)


def main_menu_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
    )


def courses_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📘 Basic Smart Money Concept — $39",
                    callback_data="basic_smc",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎓 Advanced SMC Mastery — $59",
                    callback_data="advanced_smc",
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")],
        ]
    )


def buy_now_menu(product_id: str, back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Buy Now", callback_data=f"buy_{product_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=back_to)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
    )


def payment_method_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⭐ Payment Method",
                    callback_data="bank_transfer",
                )
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
    )


def i_paid_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ I Paid", callback_data="i_paid")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
    )


def admin_payment_menu(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve_{user_id}",
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_{user_id}",
                ),
            ]
        ]
    )


def product_description(product_id: str) -> tuple[str, str]:
    if product_id == "basic_smc":
        return (
            "📘 Basic Smart Money Concept\n\n"
            "🌱 For beginners\n\n"
            "✅ Market structure\n"
            "✅ Liquidity concepts\n"
            "✅ Entry and exit basics\n"
            "✅ Risk management\n\n"
            "❌ Not included: personalized trading plan, lifetime support, "
            "or account-flip coaching.",
            "courses",
        )
    if product_id == "advanced_smc":
        return (
            "🎓 Advanced SMC Mastery\n\n"
            "🔥 For serious traders\n\n"
            "✅ Institutional direction analysis\n"
            "✅ Liquidity behavior\n"
            "✅ Advanced trade management\n"
            "✅ Psychology and discipline\n\n"
            "❌ Not included: personalized trading plan, lifetime support, "
            "or account-flip coaching.",
            "courses",
        )
    if product_id == "mentorship":
        return (
            "🤝 Private 1-to-1 Mentorship\n\n"
            "⏳ Limited slots available\n\n"
            "✅ Personalized trading plan\n"
            "✅ Live chart reviews\n"
            "✅ Account growth guidance\n"
            "✅ Lifetime support\n"
            "✅ Live Q&A sessions\n"
            "✅ Certificate of completion",
            "main_menu",
        )
    return (
        "🔐 VIP Signal\n\n"
        "📈 For serious traders who want structured guidance\n\n"
        "✅ High-probability signals\n"
        "✅ Smart Money Concept-based analysis\n"
        "✅ Daily trade alerts\n"
        "✅ Risk and money management included",
        "main_menu",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user = update.effective_user
    if not user or not update.message:
        return
    await update.message.reply_text(
        "👋 Welcome to Nisir Forex Academy.\n\n"
        "👇 Choose a service below to get started.",
        reply_markup=main_menu(user.id, settings),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "🤖 Nisir Forex Academy Help\n\n"
            "🚀 Use /start to open the main menu and choose a course, "
            "VIP Signal, or private mentorship."
        )


async def button_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    settings: Settings = context.application.bot_data["settings"]
    store: PaymentStore = context.application.bot_data["payment_store"]
    user_id = query.from_user.id
    action = query.data or ""

    if action == "main_menu":
        await query.edit_message_text(
            "🏠 Welcome to Nisir Forex Academy.\n\n"
            "👇 Choose a service below to get started.",
            reply_markup=main_menu(user_id, settings),
        )
        return

    if action == "courses":
        await query.edit_message_text(
            "📚 Courses\n\n👇 Choose your course below.",
            reply_markup=courses_menu(),
        )
        return

    if action in PRODUCTS:
        text, back_to = product_description(action)
        product = PRODUCTS[action]
        await query.edit_message_text(
            f"{text}\n\n💰 Price: {product.price}",
            reply_markup=buy_now_menu(action, back_to),
        )
        return

    if action.startswith("buy_"):
        product_id = action.removeprefix("buy_")
        product = PRODUCTS.get(product_id)
        if not product:
            await query.answer("Product not found.", show_alert=True)
            return
        store.upsert_pending(user_id, product_id, product)
        await query.edit_message_text(
            "💳 Payment\n\n"
            f"📦 Product: {product.name}\n"
            f"💰 Amount: {product.price}\n\n"
            "👇 Choose your payment method.",
            reply_markup=payment_method_menu(),
        )
        return

    if action == "bank_transfer":
        payment = store.get(user_id)
        if not payment:
            await query.answer(
                "Please select a product first.",
                show_alert=True,
            )
            return
        store.set_status(user_id, "awaiting_payment")
        await query.edit_message_text(
            "⭐ Payment Method\n\n"
            f"📦 Product: {payment['product_name']}\n"
            f"💰 Amount: {payment['price']}\n\n"
            f"👤 Account holder: {settings.cbe_account_name}\n\n"
            f"🏦 CBE: {settings.cbe_account_number}\n\n"
            f"🏦 Abyssinia Bank: {settings.abyssinia_account_number}\n\n"
            f"📱 Telebirr: {settings.telebirr_number}\n\n"
            "✅ After making the payment, click the button below.",
            reply_markup=i_paid_menu(),
        )
        return

    if action == "i_paid":
        if not store.get(user_id):
            await query.answer("Payment session not found.", show_alert=True)
            return
        store.set_status(user_id, "waiting_receipt")
        await query.edit_message_text(
            "📸 Please send a screenshot or photo of your payment receipt here."
        )
        return

    if action == "admin_dashboard":
        if user_id != settings.admin_telegram_id:
            await query.answer("Access denied.", show_alert=True)
            return
        await query.edit_message_text(
            "⚙️ Admin Dashboard\n\n"
            "📥 Payment submissions are sent here for manual review.\n\n"
            "🔎 Approve or reject each payment after checking the receipt.",
            reply_markup=main_menu_button(),
        )
        return

    if action.startswith("approve_") or action.startswith("reject_"):
        await handle_admin_decision(
            query,
            context,
            store,
            settings,
            action,
        )
        return

    if action == "my_account":
        payment = store.get(user_id)
        status = (
            f"\nLatest payment: {payment['status'].replace('_', ' ').title()}"
            if payment
            else "\nNo payment history yet."
        )
        await query.edit_message_text(
            f"👤 My Account\n\n🆔 Telegram ID: {user_id}{status}",
            reply_markup=main_menu_button(),
        )
        return

    if action == "payment_status":
        payment = store.get(user_id)
        if not payment:
            text = "💳 Payment Status\n\n📭 No payment submission found."
        else:
            text = (
                "💳 Payment Status\n\n"
                f"📦 Product: {payment['product_name']}\n"
                f"💰 Amount: {payment['price']}\n"
                f"📌 Status: {payment['status'].replace('_', ' ').title()}"
            )
        await query.edit_message_text(text, reply_markup=main_menu_button())
        return

    await query.answer("That action is no longer available.", show_alert=True)


async def handle_admin_decision(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    store: PaymentStore,
    settings: Settings,
    action: str,
) -> None:
    """Approve or reject a payment after checking that the actor is the admin."""
    admin_id = query.from_user.id
    if admin_id != settings.admin_telegram_id:
        await query.answer("Access denied.", show_alert=True)
        return

    try:
        target_user_id = int(action.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Invalid payment reference.", show_alert=True)
        return

    payment = store.get(target_user_id)
    if not payment:
        await query.answer("Payment record not found.", show_alert=True)
        return

    if action.startswith("reject_"):
        store.set_status(target_user_id, "rejected")
        await context.bot.send_message(
            chat_id=target_user_id,
            text=(
                "❌ Payment rejected.\n\n"
                "⚠️ Your payment could not be verified. "
                "Please contact support if you believe this was a mistake."
            ),
        )
        await query.edit_message_reply_markup(reply_markup=None)
        if query.message:
            await query.message.reply_text("❌ Payment rejected.")
        return

    store.set_status(target_user_id, "approved")
    if payment["product_id"] == "vip":
        try:
            invite_link = await context.bot.create_chat_invite_link(
                chat_id=settings.vip_channel_id,
                member_limit=1,
            )
            store.save_invite_link(target_user_id, invite_link.invite_link)
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "✅ Payment approved.\n\n"
                    "🔗 Your VIP invite link is ready:\n"
                    f"{invite_link.invite_link}"
                ),
            )
        except Exception:
            LOGGER.exception("Failed to create VIP invite link.")
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "✅ Payment approved.\n\n"
                    "⚠️ Your VIP access is approved, but the invite link "
                    "could not be generated automatically. Please contact support."
                ),
            )
    else:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="✅ Payment approved.\n\n🎉 Your purchase has been approved.",
        )

    await query.edit_message_reply_markup(reply_markup=None)
    if query.message:
        await query.message.reply_text("✅ Payment approved successfully.")


async def photo_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Store a receipt and send it to the admin for manual review."""
    settings: Settings = context.application.bot_data["settings"]
    store: PaymentStore = context.application.bot_data["payment_store"]
    user = update.effective_user
    message = update.message
    if not user or not message or not message.photo:
        return

    payment = store.get(user.id)
    if not payment or payment["status"] != "waiting_receipt":
        await message.reply_text(
            "📌 Please start a purchase from /start before sending a receipt."
        )
        return

    receipt_file_id = message.photo[-1].file_id
    store.save_receipt(user.id, receipt_file_id)
    await message.reply_text(
        "✅ Payment receipt received.\n\n"
        "🔎 Your payment is now under review. "
        "You will be notified once it is approved.",
        reply_markup=main_menu_button(),
    )

    try:
        await context.bot.send_message(
            chat_id=settings.admin_telegram_id,
            text=(
                "🔔 New payment submission\n\n"
                f"👤 User ID: {user.id}\n"
                f"📦 Product: {payment['product_name']}\n"
                f"💰 Amount: {payment['price']}\n\n"
                "🔎 Status: Under review"
            ),
        )
        await context.bot.send_photo(
            chat_id=settings.admin_telegram_id,
            photo=receipt_file_id,
            caption=(
                "📸 Payment receipt\n\n"
                f"👤 User ID: {user.id}\n"
                f"📦 Product: {payment['product_name']}\n"
                f"💰 Amount: {payment['price']}\n\n"
                "🔎 Please manually verify this payment."
            ),
            reply_markup=admin_payment_menu(user.id),
        )
    except Exception:
        LOGGER.exception("Failed to send payment receipt to admin.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "🚀 Use /start to open the Nisir Forex Academy menu."
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Exception while handling update %r", update, exc_info=context.error)


def build_application(settings: Settings | None = None) -> Application:
    """Create the application and register all handlers."""
    resolved_settings = settings or Settings.from_env()
    application = Application.builder().token(resolved_settings.token).build()
    application.bot_data["settings"] = resolved_settings
    application.bot_data["payment_store"] = PaymentStore(
        resolved_settings.database_path
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    """Start the bot with Telegram long polling."""
    logging.basicConfig(
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    # Request URLs contain the bot token, so never emit request-level HTTP logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    LOGGER.info("Nisir Forex Academy bot is starting")
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()