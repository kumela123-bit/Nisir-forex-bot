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

    def __init__(self, path: Path, owner_admin_id: int | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if owner_admin_id is not None:
            self.add_admin(owner_admin_id, "owner")

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    username TEXT,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vip_access (
                    user_id INTEGER PRIMARY KEY,
                    active INTEGER NOT NULL DEFAULT 1,
                    granted_at TEXT NOT NULL,
                    revoked_at TEXT
                )
                """
            )

    def upsert_user(self, user_id: int, first_name: str, username: str | None) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, first_name, username, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (user_id, first_name, username, now, now),
            )

    def list_users(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def count_users(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"])

    def list_payments(
        self,
        statuses: tuple[str, ...] | None = None,
        receipts_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[Any] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            conditions.append(f"status IN ({placeholders})")
            parameters.extend(statuses)
        if receipts_only:
            conditions.append("receipt_file_id IS NOT NULL")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM payments
                {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def payment_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM payments GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def approved_revenue(self) -> int:
        payments = self.list_payments(statuses=("approved",), limit=100000)
        return sum(parse_price(payment["price"]) for payment in payments)

    def vip_user_ids(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payments.user_id FROM payments
                WHERE payments.product_id = 'vip'
                  AND payments.status = 'approved'
                  AND NOT EXISTS (
                      SELECT 1 FROM vip_access
                      WHERE vip_access.user_id = payments.user_id
                        AND vip_access.active = 0
                  )
                UNION
                SELECT user_id FROM vip_access
                WHERE active = 1
                """
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def grant_vip(self, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vip_access (user_id, active, granted_at, revoked_at)
                VALUES (?, 1, ?, NULL)
                ON CONFLICT(user_id) DO UPDATE SET
                    active = 1,
                    granted_at = excluded.granted_at,
                    revoked_at = NULL
                """,
                (user_id, utc_now()),
            )

    def revoke_vip(self, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE vip_access
                SET active = 0, revoked_at = ?
                WHERE user_id = ?
                """,
                (utc_now(), user_id),
            )

    def course_user_ids(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id FROM payments
                WHERE product_id IN ('basic_smc', 'advanced_smc')
                  AND status = 'approved'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def mentorship_user_ids(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id FROM payments
                WHERE product_id = 'mentorship' AND status = 'approved'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def add_admin(self, user_id: int, role: str = "admin") -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admins (user_id, role, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET role = excluded.role
                """,
                (user_id, role, utc_now()),
            )

    def remove_admin(self, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))

    def is_admin(self, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row is not None

    def list_admins(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM admins ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_signal(self, content: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO signals (content, created_at) VALUES (?, ?)",
                (content, utc_now()),
            )

    def count_signals(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM signals").fetchone()
        return int(row["count"])

    def list_signals(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM signals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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


def parse_price(price: str) -> int:
    """Convert the catalog's display price such as '$39' into an integer."""
    digits = "".join(character for character in price if character.isdigit())
    return int(digits or 0)


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


def admin_dashboard_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
            [InlineKeyboardButton("💳 Payments", callback_data="admin_payments")],
            [InlineKeyboardButton("📸 Payment Receipts", callback_data="admin_receipts")],
            [InlineKeyboardButton("📚 Courses", callback_data="admin_courses")],
            [InlineKeyboardButton("🔐 VIP Signals", callback_data="admin_vip")],
            [InlineKeyboardButton("🤝 Mentorship", callback_data="admin_mentorship")],
            [InlineKeyboardButton("🔑 Access Management", callback_data="admin_access")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👤 Admins", callback_data="admin_admins")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_statistics")],
            [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
    )


def admin_back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")]]
    )


def admin_users_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 All Users", callback_data="users_all")],
            [InlineKeyboardButton("🔎 Search User", callback_data="users_search")],
            [InlineKeyboardButton("👑 VIP Users", callback_data="users_vip")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_payments_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏳ Pending Payments", callback_data="payments_pending")],
            [InlineKeyboardButton("📜 Payment History", callback_data="payments_history")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def payment_review_menu(user_id: int, has_receipt: bool) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if has_receipt:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📸 View Receipt",
                    callback_data=f"receipt_view_{user_id}",
                )
            ]
        )
    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve Payment",
                    callback_data=f"approve_{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Reject Payment",
                    callback_data=f"reject_{user_id}",
                )
            ],
            [InlineKeyboardButton("⬅️ Payments", callback_data="admin_payments")],
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def admin_receipts_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏳ Pending Receipts", callback_data="receipts_pending")],
            [InlineKeyboardButton("📜 All Receipts", callback_data="receipts_all")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_courses_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 View Courses", callback_data="courses_view")],
            [InlineKeyboardButton("➕ Add Course", callback_data="courses_add")],
            [InlineKeyboardButton("✏️ Edit Course", callback_data="courses_edit")],
            [InlineKeyboardButton("🗑 Delete Course", callback_data="courses_delete")],
            [InlineKeyboardButton("👨‍🎓 Course Students", callback_data="courses_students")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_vip_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Create VIP Signal", callback_data="vip_create")],
            [InlineKeyboardButton("📋 VIP Members", callback_data="vip_members")],
            [InlineKeyboardButton("🔓 Grant VIP Access", callback_data="vip_grant")],
            [InlineKeyboardButton("🔒 Remove VIP Access", callback_data="vip_remove")],
            [InlineKeyboardButton("📝 Manage VIP Content", callback_data="vip_content")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_mentorship_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📋 Mentorship Requests",
                    callback_data="mentorship_requests",
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 Mentorship Users",
                    callback_data="mentorship_users",
                )
            ],
            [
                InlineKeyboardButton(
                    "💰 Mentorship Price",
                    callback_data="mentorship_price",
                )
            ],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_access_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👤 View User Access", callback_data="access_view")],
            [InlineKeyboardButton("📚 Course Access", callback_data="access_course")],
            [InlineKeyboardButton("🔐 VIP Access", callback_data="access_vip")],
            [InlineKeyboardButton("🚫 Revoke Access", callback_data="access_revoke")],
            [InlineKeyboardButton("⏳ Access Status", callback_data="access_status")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_broadcast_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👥 Broadcast to All Users",
                    callback_data="broadcast_all",
                )
            ],
            [InlineKeyboardButton("👑 Broadcast to VIP", callback_data="broadcast_vip")],
            [
                InlineKeyboardButton(
                    "📚 Broadcast to Course Users",
                    callback_data="broadcast_courses",
                )
            ],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def broadcast_confirm_menu(target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Send Broadcast",
                    callback_data=f"broadcast_confirm_{target}",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="broadcast_cancel",
                ),
            ]
        ]
    )


def admin_admins_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Admin List", callback_data="admins_list")],
            [InlineKeyboardButton("➕ Add Admin", callback_data="admins_add")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data="admins_remove")],
            [
                InlineKeyboardButton(
                    "🔐 Admin Permissions",
                    callback_data="admins_permissions",
                )
            ],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_statistics_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Refresh Statistics",
                    callback_data="statistics_refresh",
                )
            ],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
        ]
    )


def admin_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👋 Welcome Message", callback_data="settings_welcome")],
            [InlineKeyboardButton("🏦 Payment Information", callback_data="settings_payment")],
            [InlineKeyboardButton("💳 Bank Details", callback_data="settings_bank")],
            [InlineKeyboardButton("🔐 VIP Price", callback_data="settings_vip_price")],
            [InlineKeyboardButton("📚 Course Settings", callback_data="settings_courses")],
            [InlineKeyboardButton("👤 Admin Settings", callback_data="settings_admins")],
            [InlineKeyboardButton("⬅️ Admin Dashboard", callback_data="admin_dashboard")],
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
        "📈 For Serious Traders Who Want Real Results\n\n"
        "✅ High-Probability Signals\n"
        "✅ Smart Money Technique Based\n"
        "✅ Daily Trade Alerts\n"
        "✅ Risk & Money Management Included",
        "main_menu",
    )


def remember_user(update: Update, store: PaymentStore) -> None:
    user = update.effective_user
    if user:
        store.upsert_user(user.id, user.first_name or "Unknown", user.username)


async def ensure_admin(
    query: Any, settings: Settings, store: PaymentStore
) -> bool:
    if not store.is_admin(query.from_user.id):
        await query.answer("⛔ Access denied.", show_alert=True)
        return False
    return True


def payment_status_label(status: str) -> str:
    return status.replace("_", " ").title()


def user_display(user: dict[str, Any] | None, user_id: int) -> str:
    if not user:
        return f"👤 Unknown user\n🆔 ID: {user_id}"
    username = f"@{user['username']}" if user.get("username") else "No username"
    state = "🚫 Blocked" if user.get("blocked") else "✅ Active"
    return (
        f"👤 {user.get('first_name', 'Unknown')}\n"
        f"🆔 ID: {user_id}\n"
        f"📛 Username: {username}\n"
        f"📌 Status: {state}"
    )


async def show_admin_dashboard(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "⚙️ Admin Dashboard\n\n👇 Choose an option below:",
        reply_markup=admin_dashboard_menu(),
    )


async def show_admin_users(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "👥 Users Management\n\n"
        f"👥 Total Users: {store.count_users()}\n"
        f"👑 VIP Users: {len(store.vip_user_ids())}\n\n"
        "👇 Choose an option:",
        reply_markup=admin_users_menu(),
    )


async def show_all_users(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    users = store.list_users()
    if not users:
        await query.edit_message_text(
            "👥 All Users\n\n📭 No users found.",
            reply_markup=admin_users_menu(),
        )
        return
    vip_ids = set(store.vip_user_ids())
    sections = ["👥 All Users\n"]
    for user in users:
        user_id = int(user["user_id"])
        membership = "👑 VIP" if user_id in vip_ids else "👤 Standard"
        sections.append(
            f"{user_display(user, user_id)}\n"
            f"🎟️ Access: {membership}\n"
        )
    await query.edit_message_text(
        "\n".join(sections),
        reply_markup=admin_users_menu(),
    )


async def show_vip_users(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    vip_ids = store.vip_user_ids()
    if not vip_ids:
        await query.edit_message_text(
            "👑 VIP Users\n\n📭 No approved VIP users found.",
            reply_markup=admin_users_menu(),
        )
        return
    sections = ["👑 VIP Users\n"]
    for user_id in vip_ids[:50]:
        sections.append(f"{user_display(store.get_user(user_id), user_id)}\n")
    await query.edit_message_text(
        "\n".join(sections),
        reply_markup=admin_users_menu(),
    )


async def show_admin_payments(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    counts = store.payment_counts()
    total = sum(counts.values())
    pending = sum(
        counts.get(status, 0)
        for status in ("pending", "awaiting_payment", "waiting_receipt", "under_review")
    )
    await query.edit_message_text(
        "💳 Payments Management\n\n"
        f"💰 Total Payments: {total}\n"
        f"⏳ Pending: {pending}\n"
        f"✅ Approved: {counts.get('approved', 0)}\n"
        f"❌ Rejected: {counts.get('rejected', 0)}\n\n"
        "👇 Choose an option:",
        reply_markup=admin_payments_menu(),
    )


async def show_pending_payments(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    payments = store.list_payments(
        statuses=("pending", "awaiting_payment", "waiting_receipt", "under_review"),
    )
    if not payments:
        await query.edit_message_text(
            "⏳ Pending Payments\n\n✅ No pending payments.",
            reply_markup=admin_payments_menu(),
        )
        return
    sections = ["⏳ Pending Payments\n"]
    keyboard: list[list[InlineKeyboardButton]] = []
    for payment in payments:
        user_id = int(payment["user_id"])
        sections.append(
            f"🆔 {user_id}\n"
            f"📦 {payment['product_name']}\n"
            f"💰 {payment['price']}\n"
            f"📌 {payment_status_label(payment['status'])}\n"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"💳 Open {user_id}",
                    callback_data=f"payment_open_{user_id}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton("⬅️ Payments", callback_data="admin_payments")]
    )
    await query.edit_message_text(
        "\n".join(sections),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_payment_history(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    payments = store.list_payments(limit=50)
    if not payments:
        await query.edit_message_text(
            "📜 Payment History\n\n📭 No payments found.",
            reply_markup=admin_payments_menu(),
        )
        return
    sections = ["📜 Payment History\n"]
    for payment in payments:
        sections.append(
            f"🆔 {payment['user_id']}\n"
            f"📦 {payment['product_name']}\n"
            f"💰 {payment['price']}\n"
            f"📊 {payment_status_label(payment['status'])}\n"
        )
    await query.edit_message_text(
        "\n".join(sections),
        reply_markup=admin_payments_menu(),
    )


async def open_payment(
    query: Any, settings: Settings, store: PaymentStore, user_id: int
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    payment = store.get(user_id)
    if not payment:
        await query.answer("⚠️ Payment not found.", show_alert=True)
        return
    await query.edit_message_text(
        "💳 Payment Details\n\n"
        f"{user_display(store.get_user(user_id), user_id)}\n\n"
        f"📦 Package: {payment['product_name']}\n"
        f"💰 Amount: {payment['price']}\n"
        f"📌 Status: {payment_status_label(payment['status'])}\n"
        f"📅 Updated: {payment['updated_at']}",
        reply_markup=payment_review_menu(
            user_id,
            bool(payment.get("receipt_file_id")),
        ),
    )


async def show_admin_receipts(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    receipts = store.list_payments(receipts_only=True, limit=100000)
    pending = sum(payment["status"] == "under_review" for payment in receipts)
    await query.edit_message_text(
        "📸 Payment Receipts\n\n"
        f"📸 Total Receipts: {len(receipts)}\n"
        f"⏳ Pending Review: {pending}\n\n"
        "👇 Choose an option:",
        reply_markup=admin_receipts_menu(),
    )


async def show_pending_receipts(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    receipts = store.list_payments(
        statuses=("under_review",),
        receipts_only=True,
    )
    if not receipts:
        await query.edit_message_text(
            "⏳ Pending Receipts\n\n✅ No pending receipts.",
            reply_markup=admin_receipts_menu(),
        )
        return
    sections = ["📸 Pending Payment Receipts\n"]
    keyboard: list[list[InlineKeyboardButton]] = []
    for payment in receipts:
        user_id = int(payment["user_id"])
        sections.append(
            f"🆔 {user_id}\n"
            f"📦 {payment['product_name']}\n"
            f"💰 {payment['price']}\n"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📸 View {user_id}",
                    callback_data=f"receipt_view_{user_id}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton("⬅️ Payment Receipts", callback_data="admin_receipts")]
    )
    await query.edit_message_text(
        "\n".join(sections),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_all_receipts(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    receipts = store.list_payments(receipts_only=True, limit=100000)
    if not receipts:
        await query.edit_message_text(
            "📸 All Receipts\n\n📭 No receipts found.",
            reply_markup=admin_receipts_menu(),
        )
        return
    sections = ["📸 All Payment Receipts\n"]
    keyboard: list[list[InlineKeyboardButton]] = []
    for payment in receipts[:50]:
        user_id = int(payment["user_id"])
        sections.append(
            f"🆔 {user_id}\n"
            f"📦 {payment['product_name']}\n"
            f"📊 {payment_status_label(payment['status'])}\n"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📸 View {user_id}",
                    callback_data=f"receipt_view_{user_id}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton("⬅️ Payment Receipts", callback_data="admin_receipts")]
    )
    await query.edit_message_text(
        "\n".join(sections),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def view_receipt(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    store: PaymentStore,
    user_id: int,
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    payment = store.get(user_id)
    receipt_file_id = payment.get("receipt_file_id") if payment else None
    if not receipt_file_id:
        await query.answer("⚠️ Receipt not found.", show_alert=True)
        return
    await context.bot.send_photo(
        chat_id=query.from_user.id,
        photo=receipt_file_id,
        caption=(
            "📸 Payment Receipt\n\n"
            f"👤 User ID: {user_id}\n"
            f"📦 Package: {payment['product_name']}\n"
            f"💰 Amount: {payment['price']}\n"
            f"📊 Status: {payment_status_label(payment['status'])}"
        ),
        reply_markup=payment_review_menu(user_id, True),
    )


async def show_admin_courses(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "📚 Courses Management\n\n👇 Manage the academy catalog below.",
        reply_markup=admin_courses_menu(),
    )


async def show_courses_admin_view(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    lines = ["📚 Available Courses\n"]
    for product_id in ("basic_smc", "advanced_smc"):
        product = PRODUCTS[product_id]
        lines.append(f"📦 {product.name}\n💰 Price: {product.price}\n")
    await query.edit_message_text(
        "\n".join(lines) + "🛠️ Use the catalog actions below.",
        reply_markup=admin_courses_menu(),
    )


async def show_course_students(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    students = store.course_user_ids()
    await query.edit_message_text(
        "👨‍🎓 Course Students\n\n"
        f"👥 Total Course Students: {len(students)}\n\n"
        "Approved course purchases are counted automatically.",
        reply_markup=admin_courses_menu(),
    )


async def show_admin_vip(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "🔐 VIP Signals Management\n\n"
        f"👑 VIP Members: {len(store.vip_user_ids())}\n"
        f"📢 Saved Signals: {store.count_signals()}\n\n"
        "👇 Choose an option:",
        reply_markup=admin_vip_menu(),
    )


async def show_vip_members_admin(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    members = store.vip_user_ids()
    if not members:
        await query.edit_message_text(
            "👑 VIP Members\n\n📭 No approved VIP members found.",
            reply_markup=admin_vip_menu(),
        )
        return
    lines = ["👑 VIP Members\n"]
    for user_id in members[:50]:
        lines.append(f"{user_display(store.get_user(user_id), user_id)}\n")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=admin_vip_menu(),
    )


async def show_admin_mentorship(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "🤝 Mentorship Management\n\n"
        f"👥 Active Users: {len(store.mentorship_user_ids())}\n"
        f"💰 Current Price: {PRODUCTS['mentorship'].price}\n\n"
        "👇 Choose an option:",
        reply_markup=admin_mentorship_menu(),
    )


async def show_mentorship_users(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    users = store.mentorship_user_ids()
    if not users:
        await query.edit_message_text(
            "🤝 Mentorship Users\n\n📭 No mentorship users found.",
            reply_markup=admin_mentorship_menu(),
        )
        return
    lines = ["🤝 Mentorship Users\n"]
    for user_id in users[:50]:
        lines.append(f"{user_display(store.get_user(user_id), user_id)}\n")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=admin_mentorship_menu(),
    )


async def show_admin_access(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "🔑 Access Management\n\n"
        "Manage course and VIP access from the options below.",
        reply_markup=admin_access_menu(),
    )


async def show_access_status(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "⏳ Access Status\n\n"
        f"👑 VIP Members: {len(store.vip_user_ids())}\n"
        f"📚 Course Students: {len(store.course_user_ids())}\n"
        f"🤝 Mentorship Users: {len(store.mentorship_user_ids())}",
        reply_markup=admin_access_menu(),
    )


async def show_admin_broadcast(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "📢 Broadcast\n\n"
        "Select a group. You will review the message before anything is sent.",
        reply_markup=admin_broadcast_menu(),
    )


async def broadcast_info(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    store: PaymentStore,
    target: str,
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    counts = {
        "all": store.count_users(),
        "vip": len(store.vip_user_ids()),
        "courses": len(store.course_user_ids()),
    }
    names = {
        "all": "👥 All Users",
        "vip": "👑 VIP Users",
        "courses": "📚 Course Users",
    }
    context.user_data["admin_action"] = {"type": "broadcast", "target": target}
    await query.edit_message_text(
        "📢 Broadcast Ready\n\n"
        f"🎯 Target: {names[target]}\n"
        f"👥 Recipients: {counts[target]}\n\n"
        "✍️ Send your message as the next message. "
        "You will be asked to confirm before delivery.",
        reply_markup=admin_back_menu(),
    )


async def show_admin_admins(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "👤 Admin Management\n\n"
        f"🛡️ Total Admins: {len(store.list_admins())}\n\n"
        "👇 Choose an option:",
        reply_markup=admin_admins_menu(),
    )


async def show_admin_list(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    admins = store.list_admins()
    lines = ["👤 Admin List\n"]
    for admin in admins:
        user_id = int(admin["user_id"])
        role = "👑 Owner" if admin["role"] == "owner" else "🛡️ Admin"
        lines.append(f"{role}\n🆔 ID: {user_id}\n")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=admin_admins_menu(),
    )


async def show_admin_statistics(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    counts = store.payment_counts()
    await query.edit_message_text(
        "📊 Nisir Forex Academy Statistics\n\n"
        f"👥 Total Users: {store.count_users()}\n"
        f"💳 Total Payments: {sum(counts.values())}\n"
        f"⏳ Pending Payments: {sum(counts.get(status, 0) for status in ('pending', 'awaiting_payment', 'waiting_receipt', 'under_review'))}\n"
        f"✅ Approved Payments: {counts.get('approved', 0)}\n"
        f"💰 Total Revenue: ${store.approved_revenue()}\n"
        f"👑 VIP Members: {len(store.vip_user_ids())}\n"
        f"📚 Course Students: {len(store.course_user_ids())}\n"
        f"🤝 Mentorship Users: {len(store.mentorship_user_ids())}",
        reply_markup=admin_statistics_menu(),
    )


async def show_admin_settings(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "⚙️ Bot Settings\n\n👇 View the current bot configuration below.",
        reply_markup=admin_settings_menu(),
    )


async def show_payment_settings(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "🏦 Payment Information\n\n"
        f"👤 Account holder: {settings.cbe_account_name}\n"
        f"🏦 CBE: {settings.cbe_account_number}\n"
        f"🏦 Abyssinia Bank: {settings.abyssinia_account_number}\n"
        f"📱 Telebirr: {settings.telebirr_number}",
        reply_markup=admin_settings_menu(),
    )


async def show_vip_price_settings(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        f"🔐 VIP Price\n\n💰 Current VIP Price: {PRODUCTS['vip'].price}",
        reply_markup=admin_settings_menu(),
    )


async def show_course_settings(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "📚 Course Settings\n\n"
        f"📘 Basic SMC: {PRODUCTS['basic_smc'].price}\n\n"
        f"🎓 Advanced SMC: {PRODUCTS['advanced_smc'].price}\n\n"
        f"🤝 Mentorship: {PRODUCTS['mentorship'].price}",
        reply_markup=admin_settings_menu(),
    )


async def show_welcome_settings(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "👋 Welcome Message\n\n"
        "👋 Welcome to Nisir Forex Academy.\n\n"
        "👇 Choose a service below to get started.",
        reply_markup=admin_settings_menu(),
    )


async def show_admin_settings_info(
    query: Any, settings: Settings, store: PaymentStore
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    await query.edit_message_text(
        "👤 Admin Settings\n\n"
        f"👑 Owner ID: {settings.admin_telegram_id}\n"
        f"🛡️ Configured Admins: {len(store.list_admins())}",
        reply_markup=admin_settings_menu(),
    )


async def show_catalog_action(
    query: Any, settings: Settings, store: PaymentStore, action: str
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    labels = {
        "courses_add": "➕ Add Course",
        "courses_edit": "✏️ Edit Course",
        "courses_delete": "🗑️ Delete Course",
    }
    await query.edit_message_text(
        f"{labels[action]}\n\n"
        "📚 The current catalog is defined in PRODUCTS and is ready for use.\n"
        "To change prices or course content, update the catalog configuration.",
        reply_markup=admin_courses_menu(),
    )


async def send_broadcast(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    store: PaymentStore,
    target: str,
) -> None:
    if not await ensure_admin(query, settings, store):
        return
    draft = context.user_data.pop("broadcast_draft", None)
    context.user_data.pop("admin_action", None)
    if not draft:
        await query.answer("⚠️ Broadcast draft not found.", show_alert=True)
        return

    if target == "all":
        recipients = [
            int(user["user_id"])
            for user in store.list_users(limit=100000)
            if not user.get("blocked")
        ]
    elif target == "vip":
        recipients = store.vip_user_ids()
    elif target == "courses":
        recipients = store.course_user_ids()
    else:
        await query.answer("⚠️ Invalid broadcast target.", show_alert=True)
        return

    sent = 0
    failed = 0
    for recipient_id in dict.fromkeys(recipients):
        try:
            await context.bot.send_message(
                chat_id=recipient_id,
                text=f"📢 Nisir Forex Academy\n\n{draft}",
            )
            sent += 1
        except Exception:
            failed += 1
            LOGGER.warning("Broadcast delivery failed for user %s", recipient_id)

    await query.edit_message_text(
        "📢 Broadcast Complete\n\n"
        f"✅ Sent: {sent}\n"
        f"⚠️ Failed: {failed}",
        reply_markup=admin_broadcast_menu(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: PaymentStore = context.application.bot_data["payment_store"]
    user = update.effective_user
    if not user or not update.message:
        return
    remember_user(update, store)
    await update.message.reply_text(
        "👋 Welcome to Nisir Forex Academy.\n\n"
        "👇 Choose a service below to get started.",
        reply_markup=main_menu(user.id, settings),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: PaymentStore = context.application.bot_data["payment_store"]
    remember_user(update, store)
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
    remember_user(update, store)

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

    if action == "vip_signal":
        vip_product = PRODUCTS.get("vip")
        if vip_product is None:
            LOGGER.error("VIP product is missing from the PRODUCTS catalog")
            return
        await query.edit_message_text(
            "🔐 VIP Signal\n\n"
            "📈 For Serious Traders Who Want Real Results\n\n"
            "✅ High-Probability Signals\n"
            "✅ Smart Money Technique Based\n"
            "✅ Daily Trade Alerts\n"
            "✅ Risk & Money Management Included\n\n"
            f"💰 Price: {vip_product.price}",
            reply_markup=buy_now_menu("vip", "main_menu"),
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
        product_id = action[len("buy_") :]
        product = PRODUCTS.get(product_id)
        if not product:
            await query.answer("⚠️ Product unavailable.", show_alert=True)
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
        await show_admin_dashboard(query, settings, store)
        return

    if action == "admin_users":
        await show_admin_users(query, settings, store)
        return
    if action == "users_all":
        await show_all_users(query, settings, store)
        return
    if action == "users_vip":
        await show_vip_users(query, settings, store)
        return
    if action == "users_search":
        if await ensure_admin(query, settings, store):
            context.user_data["admin_action"] = {"type": "user_search"}
            await query.edit_message_text(
                "🔎 Search User\n\n✍️ Send a Telegram ID, username, or name.",
                reply_markup=admin_back_menu(),
            )
        return

    if action == "admin_payments":
        await show_admin_payments(query, settings, store)
        return
    if action == "payments_pending":
        await show_pending_payments(query, settings, store)
        return
    if action == "payments_history":
        await show_payment_history(query, settings, store)
        return
    if action.startswith("payment_open_"):
        try:
            payment_user_id = int(action.removeprefix("payment_open_"))
        except ValueError:
            await query.answer("⚠️ Invalid payment reference.", show_alert=True)
            return
        await open_payment(query, settings, store, payment_user_id)
        return

    if action == "admin_receipts":
        await show_admin_receipts(query, settings, store)
        return
    if action == "receipts_pending":
        await show_pending_receipts(query, settings, store)
        return
    if action == "receipts_all":
        await show_all_receipts(query, settings, store)
        return
    if action.startswith("receipt_view_"):
        try:
            receipt_user_id = int(action.removeprefix("receipt_view_"))
        except ValueError:
            await query.answer("⚠️ Invalid receipt reference.", show_alert=True)
            return
        await view_receipt(
            query,
            context,
            settings,
            store,
            receipt_user_id,
        )
        return

    if action == "admin_courses":
        await show_admin_courses(query, settings, store)
        return
    if action == "courses_view":
        await show_courses_admin_view(query, settings, store)
        return
    if action == "courses_students":
        await show_course_students(query, settings, store)
        return
    if action in {"courses_add", "courses_edit", "courses_delete"}:
        await show_catalog_action(query, settings, store, action)
        return

    if action == "admin_vip":
        await show_admin_vip(query, settings, store)
        return
    if action == "vip_members":
        await show_vip_members_admin(query, settings, store)
        return
    if action == "vip_content":
        if await ensure_admin(query, settings, store):
            signals = store.list_signals()
            if not signals:
                text = "📝 VIP Content\n\n📭 No VIP signals have been saved."
            else:
                text = "📝 Recent VIP Content\n\n" + "\n\n".join(
                    f"📢 {signal['content']}" for signal in signals
                )
            await query.edit_message_text(text, reply_markup=admin_vip_menu())
        return
    if action == "vip_create":
        if await ensure_admin(query, settings, store):
            context.user_data["admin_action"] = {"type": "vip_signal"}
            await query.edit_message_text(
                "📢 Create VIP Signal\n\n✍️ Send the signal text as your next message.",
                reply_markup=admin_vip_menu(),
            )
        return
    if action in {"vip_grant", "vip_remove"}:
        if await ensure_admin(query, settings, store):
            context.user_data["admin_action"] = {"type": action}
            label = "grant" if action == "vip_grant" else "remove"
            await query.edit_message_text(
                f"{'🔓' if action == 'vip_grant' else '🔒'} "
                f"{label.title()} VIP Access\n\n"
                "✍️ Send the target user's Telegram ID.",
                reply_markup=admin_vip_menu(),
            )
        return

    if action == "admin_mentorship":
        await show_admin_mentorship(query, settings, store)
        return
    if action == "mentorship_users":
        await show_mentorship_users(query, settings, store)
        return
    if action == "mentorship_requests":
        if await ensure_admin(query, settings, store):
            requests = [
                payment
                for payment in store.list_payments(limit=100000)
                if payment["product_id"] == "mentorship"
                and payment["status"] not in {"approved", "rejected"}
            ]
            text = (
                "📋 Mentorship Requests\n\n"
                + (
                    "\n\n".join(
                        f"🆔 {payment['user_id']}\n"
                        f"💰 {payment['price']}\n"
                        f"📌 {payment_status_label(payment['status'])}"
                        for payment in requests
                    )
                    if requests
                    else "📭 No pending mentorship requests."
                )
            )
            await query.edit_message_text(
                text,
                reply_markup=admin_mentorship_menu(),
            )
        return
    if action == "mentorship_price":
        if await ensure_admin(query, settings, store):
            await query.edit_message_text(
                f"💰 Mentorship Price\n\n"
                f"Current price: {PRODUCTS['mentorship'].price}",
                reply_markup=admin_mentorship_menu(),
            )
        return

    if action == "admin_access":
        await show_admin_access(query, settings, store)
        return
    if action == "access_status":
        await show_access_status(query, settings, store)
        return
    if action in {"access_view", "access_revoke"}:
        if await ensure_admin(query, settings, store):
            context.user_data["admin_action"] = {"type": action}
            await query.edit_message_text(
                f"{'👤 View User Access' if action == 'access_view' else '🚫 Revoke Access'}"
                "\n\n✍️ Send the target user's Telegram ID.",
                reply_markup=admin_access_menu(),
            )
        return
    if action == "access_course":
        if await ensure_admin(query, settings, store):
            await query.edit_message_text(
                "📚 Course Access\n\n"
                f"👥 Approved course users: {len(store.course_user_ids())}",
                reply_markup=admin_access_menu(),
            )
        return
    if action == "access_vip":
        if await ensure_admin(query, settings, store):
            await query.edit_message_text(
                "🔐 VIP Access\n\n"
                f"👑 Approved VIP users: {len(store.vip_user_ids())}",
                reply_markup=admin_access_menu(),
            )
        return

    if action == "admin_broadcast":
        await show_admin_broadcast(query, settings, store)
        return
    if action in {"broadcast_all", "broadcast_vip", "broadcast_courses"}:
        await broadcast_info(
            query,
            context,
            settings,
            store,
            action.removeprefix("broadcast_"),
        )
        return
    if action == "broadcast_cancel":
        context.user_data.pop("admin_action", None)
        context.user_data.pop("broadcast_draft", None)
        await query.edit_message_text(
            "📢 Broadcast cancelled.",
            reply_markup=admin_broadcast_menu(),
        )
        return
    if action.startswith("broadcast_confirm_"):
        target = action.removeprefix("broadcast_confirm_")
        await send_broadcast(query, context, settings, store, target)
        return

    if action == "admin_admins":
        await show_admin_admins(query, settings, store)
        return
    if action == "admins_list":
        await show_admin_list(query, settings, store)
        return
    if action in {"admins_add", "admins_remove"}:
        if await ensure_admin(query, settings, store):
            context.user_data["admin_action"] = {"type": action}
            label = "add" if action == "admins_add" else "remove"
            await query.edit_message_text(
                f"{'➕' if action == 'admins_add' else '➖'} "
                f"{label.title()} Admin\n\n✍️ Send the target user's Telegram ID.",
                reply_markup=admin_admins_menu(),
            )
        return
    if action == "admins_permissions":
        if await ensure_admin(query, settings, store):
            await query.edit_message_text(
                "🔐 Admin Permissions\n\n"
                "🛡️ All configured admins can manage users, payments, "
                "access, broadcasts, and settings.",
                reply_markup=admin_admins_menu(),
            )
        return

    if action in {"admin_statistics", "statistics_refresh"}:
        await show_admin_statistics(query, settings, store)
        return

    if action == "admin_settings":
        await show_admin_settings(query, settings, store)
        return
    if action == "settings_welcome":
        await show_welcome_settings(query, settings, store)
        return
    if action in {"settings_payment", "settings_bank"}:
        await show_payment_settings(query, settings, store)
        return
    if action == "settings_vip_price":
        await show_vip_price_settings(query, settings, store)
        return
    if action == "settings_courses":
        await show_course_settings(query, settings, store)
        return
    if action == "settings_admins":
        await show_admin_settings_info(query, settings, store)
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
    if not store.is_admin(admin_id):
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
            store.grant_vip(target_user_id)
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
    remember_user(update, store)

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
    settings: Settings = context.application.bot_data["settings"]
    store: PaymentStore = context.application.bot_data["payment_store"]
    message = update.message
    user = update.effective_user
    if not message or not user or not message.text:
        return
    remember_user(update, store)

    pending_action = context.user_data.get("admin_action")
    if not pending_action:
        await message.reply_text(
            "🚀 Use /start to open the Nisir Forex Academy menu."
        )
        return

    if not store.is_admin(user.id):
        context.user_data.pop("admin_action", None)
        context.user_data.pop("broadcast_draft", None)
        await message.reply_text("⛔ You are not authorized to use that action.")
        return

    action_type = pending_action.get("type")
    text = message.text.strip()

    if action_type == "broadcast":
        target = pending_action.get("target")
        if target not in {"all", "vip", "courses"}:
            context.user_data.pop("admin_action", None)
            await message.reply_text("⚠️ Invalid broadcast target.")
            return
        context.user_data["broadcast_draft"] = text
        context.user_data["admin_action"] = {
            "type": "broadcast_confirmation",
            "target": target,
        }
        await message.reply_text(
            "📝 Broadcast Preview\n\n"
            f"{text}\n\n"
            "⚠️ Confirm delivery to the selected audience.",
            reply_markup=broadcast_confirm_menu(target),
        )
        return

    if action_type == "broadcast_confirmation":
        await message.reply_text(
            "⚠️ Please use the confirmation buttons above.",
            reply_markup=broadcast_confirm_menu(pending_action["target"]),
        )
        return

    if action_type == "vip_signal":
        store.save_signal(text)
        context.user_data.pop("admin_action", None)
        await message.reply_text(
            "✅ VIP signal saved successfully.",
            reply_markup=admin_vip_menu(),
        )
        return

    if action_type == "user_search":
        context.user_data.pop("admin_action", None)
        users = store.list_users(limit=100000)
        normalized = text.removeprefix("@").lower()
        matches = [
            registered_user
            for registered_user in users
            if normalized in str(registered_user["user_id"]).lower()
            or normalized in str(registered_user["first_name"]).lower()
            or normalized in str(registered_user.get("username") or "").lower()
        ]
        if not matches:
            await message.reply_text(
                "🔎 No matching users found.",
                reply_markup=admin_users_menu(),
            )
            return
        await message.reply_text(
            "🔎 User Search Results\n\n"
            + "\n\n".join(
                user_display(found_user, int(found_user["user_id"]))
                for found_user in matches[:20]
            ),
            reply_markup=admin_users_menu(),
        )
        return

    if action_type in {"admins_add", "admins_remove"}:
        context.user_data.pop("admin_action", None)
        try:
            target_user_id = int(text)
        except ValueError:
            await message.reply_text(
                "⚠️ Send a valid numeric Telegram ID.",
                reply_markup=admin_admins_menu(),
            )
            return
        if action_type == "admins_add":
            store.add_admin(target_user_id)
            result = f"✅ User {target_user_id} is now an admin."
        elif target_user_id == settings.admin_telegram_id:
            result = "⚠️ The owner admin cannot be removed."
        else:
            store.remove_admin(target_user_id)
            result = f"✅ Admin access removed for {target_user_id}."
        await message.reply_text(result, reply_markup=admin_admins_menu())
        return

    if action_type in {"vip_grant", "vip_remove"}:
        context.user_data.pop("admin_action", None)
        try:
            target_user_id = int(text)
        except ValueError:
            await message.reply_text(
                "⚠️ Send a valid numeric Telegram ID.",
                reply_markup=admin_vip_menu(),
            )
            return
        try:
            if action_type == "vip_grant":
                invite_link = await context.bot.create_chat_invite_link(
                    chat_id=settings.vip_channel_id,
                    member_limit=1,
                )
                store.grant_vip(target_user_id)
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=(
                        "🔓 VIP access granted.\n\n"
                        f"🔗 Join the VIP channel: {invite_link.invite_link}"
                    ),
                )
                result = "✅ VIP access granted and invite link sent."
            else:
                store.revoke_vip(target_user_id)
                await context.bot.ban_chat_member(
                    chat_id=settings.vip_channel_id,
                    user_id=target_user_id,
                )
                result = "✅ VIP access revoked and user removed from the channel."
        except Exception:
            LOGGER.exception("VIP access action failed for user %s", target_user_id)
            result = (
                "⚠️ The VIP access record was updated, but Telegram could not "
                "complete the channel action. Check the bot's channel permissions."
            )
        await message.reply_text(result, reply_markup=admin_vip_menu())
        return

    if action_type in {"access_view", "access_revoke"}:
        context.user_data.pop("admin_action", None)
        try:
            target_user_id = int(text)
        except ValueError:
            await message.reply_text(
                "⚠️ Send a valid numeric Telegram ID.",
                reply_markup=admin_access_menu(),
            )
            return
        if action_type == "access_revoke":
            store.revoke_vip(target_user_id)
            payment = store.get(target_user_id)
            if payment and payment["status"] == "approved":
                store.set_status(target_user_id, "revoked")
            result = "✅ VIP access has been revoked where applicable."
        else:
            payment = store.get(target_user_id)
            result = (
                f"👤 User Access\n\n{user_display(store.get_user(target_user_id), target_user_id)}\n\n"
                f"👑 VIP: {'Yes' if target_user_id in store.vip_user_ids() else 'No'}\n"
                f"📚 Course: {'Yes' if target_user_id in store.course_user_ids() else 'No'}\n"
                f"🤝 Mentorship: {'Yes' if target_user_id in store.mentorship_user_ids() else 'No'}\n"
                f"💳 Latest Payment: {payment_status_label(payment['status']) if payment else 'None'}"
            )
        await message.reply_text(result, reply_markup=admin_access_menu())
        return

    context.user_data.pop("admin_action", None)
    await message.reply_text(
        "ℹ️ That admin action is not available yet.",
        reply_markup=admin_dashboard_menu(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Exception while handling update %r", update, exc_info=context.error)


def build_application(settings: Settings | None = None) -> Application:
    """Create the application and register all handlers."""
    resolved_settings = settings or Settings.from_env()
    application = Application.builder().token(resolved_settings.token).build()
    application.bot_data["settings"] = resolved_settings
    application.bot_data["payment_store"] = PaymentStore(
        resolved_settings.database_path,
        resolved_settings.admin_telegram_id,
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