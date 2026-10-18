import os
import sys
import html
import time
import shutil
import logging
import asyncio
import tempfile
import platform
from datetime import datetime, timedelta
from typing import Optional

import psutil
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    constants,
)
from telegram.ext import ContextTypes

from .storage import BotDatabase, DATA_DIR

load_dotenv()
logger = logging.getLogger(__name__)
storage = BotDatabase.get_instance()
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

def get_admin_ids() -> list[int]:
    raw = os.getenv("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return []
    ids = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if item.isdigit():
            ids.append(int(item))
    return ids

def is_admin(user_id: int) -> bool:
    return user_id in get_admin_ids()

def format_uptime(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    return f"{hours:02d}h {minutes:02d}m {sec:02d}s"

def get_system_metrics() -> dict:
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(".")

    return {
        "cpu": cpu_percent,
        "mem_percent": mem.percent,
        "mem_used_mb": mem.used // (1024 * 1024),
        "mem_total_mb": mem.total // (1024 * 1024),
        "disk_free_gb": f"{disk.free / (1024 ** 3):.1f}",
        "disk_total_gb": f"{disk.total / (1024 ** 3):.1f}",
        "python_version": platform.python_version(),
        "os_name": f"{platform.system()} {platform.release()}",
    }

def render_admin_dashboard() -> tuple[str, InlineKeyboardMarkup]:
    uptime = format_uptime(time.time() - storage.bot_started_at)
    metrics = get_system_metrics()
    users_count = storage.get_users_count()
    downloads_count = storage.get_total_downloads()
    banned_count = storage.get_banned_count()

    text = (
        "<blockquote>🛡️ <b>Admin Control Center</b></blockquote>\n\n"
        f"👥 <b>Total Users:</b> <code>{users_count}</code>\n"
        f"📥 <b>Total Downloads:</b> <code>{downloads_count}</code>\n"
        f"🚫 <b>Banned Users:</b> <code>{banned_count}</code>\n"
        f"⏱️ <b>Bot Uptime:</b> <code>{uptime}</code>\n\n"
        "<blockquote>🖥️ <b>Host System Status</b></blockquote>\n"
        f"• <b>CPU Usage:</b> <code>{metrics['cpu']}%</code>\n"
        f"• <b>RAM Usage:</b> <code>{metrics['mem_used_mb']} MB / {metrics['mem_total_mb']} MB ({metrics['mem_percent']}%)</code>\n"
        f"• <b>Disk Free:</b> <code>{metrics['disk_free_gb']} GB / {metrics['disk_total_gb']} GB</code>\n"
        f"• <b>OS:</b> <code>{metrics['os_name']}</code>\n\n"
        "<blockquote>👇 <i>Quick Management Actions:</i></blockquote>"
    )

    keyboard = [
        [
            InlineKeyboardButton("📥 Downloading Stats", callback_data="admin:dl_stats"),
            InlineKeyboardButton("⚡ Ongoing Downloads", callback_data="admin:ongoing"),
        ],
        [
            InlineKeyboardButton("📊 System Stats", callback_data="admin:stats"),
            InlineKeyboardButton("📜 View Logs", callback_data="admin:logs"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast_help"),
            InlineKeyboardButton("🧹 Clear Temp", callback_data="admin:clearcache"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="admin:refresh"),
            InlineKeyboardButton("❌ Close", callback_data="admin:close"),
        ],
    ]
    return text, InlineKeyboardMarkup(keyboard)

# ----------------- Admin Commands -----------------

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> This command is restricted to administrators.", parse_mode=constants.ParseMode.HTML)
        return

    text, markup = render_admin_dashboard()
    await update.message.reply_text(text, reply_markup=markup, parse_mode=constants.ParseMode.HTML)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> Admin only command.", parse_mode=constants.ParseMode.HTML)
        return

    uptime = format_uptime(time.time() - storage.bot_started_at)
    metrics = get_system_metrics()
    users_count = storage.get_users_count()
    downloads_count = storage.get_total_downloads()
    banned_count = storage.get_banned_count()

    text = (
        "<blockquote>📊 <b>Universal Songs Downloader — Bot Statistics</b></blockquote>\n\n"
        f"👥 <b>Active Registered Users:</b> <code>{users_count}</code>\n"
        f"🎵 <b>Total Music Tracks Downloaded:</b> <code>{downloads_count}</code>\n"
        f"🚫 <b>Banned User Accounts:</b> <code>{banned_count}</code>\n"
        f"⏱️ <b>Online Uptime:</b> <code>{uptime}</code>\n\n"
        "<blockquote>🖥️ <b>Server Resources</b></blockquote>\n"
        f"• <b>CPU:</b> <code>{metrics['cpu']}%</code>\n"
        f"• <b>Memory:</b> <code>{metrics['mem_used_mb']} MB / {metrics['mem_total_mb']} MB ({metrics['mem_percent']}%)</code>\n"
        f"• <b>Storage:</b> <code>{metrics['disk_free_gb']} GB Free</code>\n"
        f"• <b>Python:</b> <code>{metrics['python_version']}</code>\n"
        f"• <b>Platform:</b> <code>{metrics['os_name']}</code>"
    )
    await update.message.reply_text(text, parse_mode=constants.ParseMode.HTML)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> Admin only command.", parse_mode=constants.ParseMode.HTML)
        return

    reply = update.message.reply_to_message
    broadcast_text = " ".join(context.args) if context.args else ""

    if not reply and not broadcast_text:
        help_msg = (
            "<blockquote>📢 <b>Broadcast Command Guide</b></blockquote>\n\n"
            "You can broadcast messages to all bot users in two ways:\n\n"
            "<b>1. Text Broadcast:</b>\n"
            "<code>/broadcast Hello everyone! New updates are live.</code>\n\n"
            "<b>2. Media Broadcast (Photo / Audio / Video / Sticker):</b>\n"
            "Reply to any message with <code>/broadcast</code> and it will be forwarded to all users!"
        )
        await update.message.reply_text(help_msg, parse_mode=constants.ParseMode.HTML)
        return

    user_ids = storage.get_broadcast_user_ids()
    total_users = len(user_ids)
    if total_users == 0:
        await update.message.reply_text("ℹ️ No users registered yet in the database.")
        return

    status_msg = await update.message.reply_text(
        f"🚀 <b>Starting Broadcast to {total_users} users...</b>",
        parse_mode=constants.ParseMode.HTML,
    )

    sent = 0
    failed = 0
    last_edit = time.time()

    for idx, uid in enumerate(user_ids, 1):
        try:
            if reply:
                await context.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=update.effective_chat.id,
                    message_id=reply.message_id,
                )
            else:
                await context.bot.send_message(
                    chat_id=uid,
                    text=broadcast_text,
                    parse_mode=constants.ParseMode.HTML,
                )
            sent += 1
        except Exception as e:
            logger.debug(f"Broadcast failed for user {uid}: {e}")
            failed += 1

        # Throttle Telegram rate limit
        await asyncio.sleep(0.05)

        # Update progress every 3 seconds
        if time.time() - last_edit > 3.0:
            last_edit = time.time()
            try:
                await status_msg.edit_text(
                    f"📢 <b>Broadcasting:</b> {idx}/{total_users}\n"
                    f"✅ <b>Sent:</b> {sent} | ❌ <b>Failed:</b> {failed}",
                    parse_mode=constants.ParseMode.HTML,
                )
            except Exception:
                pass

    summary = (
        "<blockquote>📢 <b>Broadcast Completed!</b></blockquote>\n\n"
        f"👥 <b>Total Target Users:</b> <code>{total_users}</code>\n"
        f"✅ <b>Delivered Successfully:</b> <code>{sent}</code>\n"
        f"❌ <b>Blocked / Failed:</b> <code>{failed}</code>"
    )
    await status_msg.edit_text(summary, parse_mode=constants.ParseMode.HTML)

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> Admin only command.", parse_mode=constants.ParseMode.HTML)
        return

    lines_count = 25
    if context.args and context.args[0].isdigit():
        lines_count = min(int(context.args[0]), 100)

    if not os.path.exists(LOG_FILE):
        await update.message.reply_text("ℹ️ Log file is not yet available on disk.")
        return

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines_count:]
            log_content = "".join(recent_lines).strip()

        if not log_content:
            await update.message.reply_text("ℹ️ Log file is currently empty.")
            return

        if len(log_content) > 3500:
            log_content = log_content[-3500:]

        escaped = html.escape(log_content)
        await update.message.reply_text(
            f"<blockquote>📜 <b>Recent Bot Logs (Last {lines_count} lines)</b></blockquote>\n\n"
            f"<pre><code class=\"language-log\">{escaped}</code></pre>",
            parse_mode=constants.ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error reading logs: {html.escape(str(e))}")

async def sendlog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    if not os.path.exists(LOG_FILE):
        await update.message.reply_text("ℹ️ Log file does not exist yet.")
        return

    with open(LOG_FILE, "rb") as f:
        await context.bot.send_document(
            chat_id=user_id,
            document=f,
            filename="bot.log",
            caption="📜 <b>Bot Execution Log File</b>",
            parse_mode=constants.ParseMode.HTML,
        )

async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    from .downloader import get_cookie_file
    cookie_path = get_cookie_file()

    if cookie_path and os.path.exists(cookie_path):
        size_kb = os.path.getsize(cookie_path) / 1024
        msg = (
            "<blockquote>🍪 <b>YouTube Cookies: Active</b></blockquote>\n\n"
            f"• <b>Status:</b> ✅ Installed & Active\n"
            f"• <b>Location:</b> <code>{cookie_path}</code>\n"
            f"• <b>Size:</b> <code>{size_kb:.1f} KB</code>\n\n"
            "<i>To update, simply send a new <code>cookies.txt</code> file to this chat.</i>"
        )
    else:
        msg = (
            "<blockquote>🍪 <b>YouTube Cookies: Not Found</b></blockquote>\n\n"
            "• <b>Status:</b> ⚠️ Missing\n\n"
            "<b>How to add cookies to bypass datacenter IP restrictions:</b>\n"
            "1. Export cookies from your browser using the Chrome/Firefox extension <b>Get cookies.txt locally</b> on YouTube.\n"
            "2. Send the exported <code>cookies.txt</code> file directly as a document to this chat.\n"
            "3. The bot will automatically save and activate it!"
        )
    await update.message.reply_text(msg, parse_mode=constants.ParseMode.HTML)

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/ban &lt;user_id&gt;</code>", parse_mode=constants.ParseMode.HTML)
        return

    target_id = int(context.args[0])
    if is_admin(target_id):
        await update.message.reply_text("⚠️ Cannot ban an administrator.")
        return

    storage.ban_user(target_id)
    await update.message.reply_text(f"🚫 User <code>{target_id}</code> has been banned from using the bot.", parse_mode=constants.ParseMode.HTML)

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/unban &lt;user_id&gt;</code>", parse_mode=constants.ParseMode.HTML)
        return

    target_id = int(context.args[0])
    if storage.unban_user(target_id):
        await update.message.reply_text(f"✅ User <code>{target_id}</code> has been unbanned.", parse_mode=constants.ParseMode.HTML)
    else:
        await update.message.reply_text(f"ℹ️ User <code>{target_id}</code> was not banned or not found.", parse_mode=constants.ParseMode.HTML)

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/userinfo &lt;user_id&gt;</code>", parse_mode=constants.ParseMode.HTML)
        return

    target_id = int(context.args[0])
    info = storage.get_user_info(target_id)
    if not info:
        await update.message.reply_text(f"ℹ️ User <code>{target_id}</code> not found in database.", parse_mode=constants.ParseMode.HTML)
        return

    status = "🚫 Banned" if info.get("is_banned") else "✅ Active"
    msg = (
        f"<blockquote>👤 <b>User Info: {target_id}</b></blockquote>\n\n"
        f"• <b>Username:</b> @{html.escape(info.get('username') or 'N/A')}\n"
        f"• <b>First Name:</b> {html.escape(info.get('first_name') or 'N/A')}\n"
        f"• <b>Joined:</b> <code>{info.get('joined_at', 'N/A')}</code>\n"
        f"• <b>Last Active:</b> <code>{info.get('last_active', 'N/A')}</code>\n"
        f"• <b>Total Downloads:</b> <code>{info.get('downloads', 0)}</code>\n"
        f"• <b>Account Status:</b> <b>{status}</b>"
    )
    await update.message.reply_text(msg, parse_mode=constants.ParseMode.HTML)

async def clearcache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    temp_root = tempfile.gettempdir()
    deleted = 0
    errors = 0

    for item in os.listdir(temp_root):
        if item.startswith("spotibot_"):
            full_path = os.path.join(temp_root, item)
            try:
                if os.path.isdir(full_path):
                    shutil.rmtree(full_path, ignore_errors=True)
                else:
                    os.remove(full_path)
                deleted += 1
            except Exception:
                errors += 1

    await update.message.reply_text(
        f"🧹 <b>Cache Cleaned:</b> Removed {deleted} temporary directories. (Failed: {errors})",
        parse_mode=constants.ParseMode.HTML,
    )

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=constants.ParseMode.HTML)
        return

    await update.message.reply_text("🔄 <b>Restarting Universal Songs Downloader bot...</b>", parse_mode=constants.ParseMode.HTML)

    def restart_process():
        time.sleep(1.0)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    import threading
    threading.Thread(target=restart_process, daemon=True).start()

# ----------------- Admin Callback Query Router -----------------

async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.answer("⛔ Access denied.", show_alert=True)
        return

    data = query.data
    await query.answer()

    if data == "admin:refresh":
        text, markup = render_admin_dashboard()
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=constants.ParseMode.HTML)
        except Exception:
            pass

    elif data == "admin:dl_stats":
        dl_stats = storage.get_downloading_stats()
        top_lines = []
        for idx, u in enumerate(dl_stats["top_today"], start=1):
            name = u.get("first_name") or u.get("username") or f"User {u['user_id']}"
            top_lines.append(f"  <b>{idx}.</b> {html.escape(name)} — <code>{u['daily_downloads']}/{u.get('custom_daily_limit', 5)}</code> dl")

        top_display = "\n".join(top_lines) if top_lines else "  <i>No downloads yet today.</i>"

        text = (
            "<blockquote>📥 <b>Downloading Statistics & Analytics</b></blockquote>\n\n"
            f"📅 <b>Today's Total Downloads:</b> <code>{dl_stats['today_downloads']}</code>\n"
            f"👥 <b>Active Users Today:</b> <code>{dl_stats['today_users']}</code>\n"
            f"⚡ <b>All-Time Downloads:</b> <code>{dl_stats['total_downloads']}</code>\n"
            f"💾 <b>Cached Songs (Instant CDN):</b> <code>{dl_stats['cached_songs']}</code>\n\n"
            "<blockquote>🏆 <b>Top Downloaders Today:</b></blockquote>\n"
            f"{top_display}"
        )
        back_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="admin:dl_stats")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin:refresh")],
        ])
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode=constants.ParseMode.HTML)

    elif data == "admin:ongoing":
        # Check active temp spotibot directories
        temp_root = tempfile.gettempdir()
        active_jobs = []
        try:
            for item in os.listdir(temp_root):
                if item.startswith("spotibot_"):
                    folder_path = os.path.join(temp_root, item)
                    if os.path.isdir(folder_path):
                        mtime = os.path.getmtime(folder_path)
                        age_sec = int(time.time() - mtime)
                        files = os.listdir(folder_path)
                        file_info = []
                        for f in files:
                            fp = os.path.join(folder_path, f)
                            sz_mb = os.path.getsize(fp) / (1024 * 1024) if os.path.exists(fp) else 0
                            file_info.append(f"{f} ({sz_mb:.1f} MB)")
                        active_jobs.append((item, age_sec, file_info))
        except Exception as e:
            logger.warning(f"Error checking ongoing downloads: {e}")

        if not active_jobs:
            text = (
                "<blockquote>⚡ <b>Ongoing Active Downloads</b></blockquote>\n\n"
                "✅ <i>No downloads are currently in progress. System is idle.</i>"
            )
        else:
            lines = [f"<blockquote>⚡ <b>Ongoing Active Downloads ({len(active_jobs)} Active):</b></blockquote>\n"]
            for job_id, age, files in active_jobs:
                f_str = ", ".join(files) if files else "resolving audio stream..."
                lines.append(f"• 📁 <code>{job_id}</code> (running for {age}s)\n  └ <i>{html.escape(f_str)}</i>")
            text = "\n".join(lines)

        back_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="admin:ongoing")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin:refresh")],
        ])
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode=constants.ParseMode.HTML)

    elif data.startswith("admin:set_limit:"):
        # Format: admin:set_limit:<target_user_id>:<amount>
        parts = data.split(":")
        target_uid = int(parts[2])
        amount = int(parts[3])

        storage.set_user_daily_limit(target_uid, amount)
        user_info = storage.get_user_info(target_uid) or {}
        user_name = user_info.get("first_name") or user_info.get("username") or str(target_uid)

        confirm_text = (
            f"✅ <b>Limit Updated!</b>\n\n"
            f"User: <b>{html.escape(user_name)}</b> (<code>{target_uid}</code>)\n"
            f"New Daily Limit: <b>{amount} songs/day</b>"
        )
        try:
            await query.edit_message_text(confirm_text, parse_mode=constants.ParseMode.HTML)
        except Exception:
            await query.message.reply_text(confirm_text, parse_mode=constants.ParseMode.HTML)

        # Notify the user directly
        user_alert = (
            "<blockquote>🎉 <b>Download Limit Increased!</b></blockquote>\n\n"
            "An administrator has approved your request.\n"
            f"Your new daily limit is now: <b>{amount} songs/day</b>! 🎵"
        )
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=user_alert,
                parse_mode=constants.ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Could not notify user {target_uid} of limit increase: {e}")

    elif data == "admin:stats":
        metrics = get_system_metrics()
        uptime = format_uptime(time.time() - storage.bot_started_at)
        text = (
            "<blockquote>📊 <b>Detailed System & Bot Metrics</b></blockquote>\n\n"
            f"👥 <b>Users:</b> <code>{storage.get_users_count()}</code>\n"
            f"🎵 <b>Downloads:</b> <code>{storage.get_total_downloads()}</code>\n"
            f"⏱️ <b>Uptime:</b> <code>{uptime}</code>\n"
            f"🖥️ <b>CPU:</b> <code>{metrics['cpu']}%</code>\n"
            f"💾 <b>RAM:</b> <code>{metrics['mem_used_mb']} MB / {metrics['mem_total_mb']} MB ({metrics['mem_percent']}%)</code>\n"
            f"📦 <b>Disk:</b> <code>{metrics['disk_free_gb']} GB Free / {metrics['disk_total_gb']} GB</code>\n"
            f"🐍 <b>Python:</b> <code>{metrics['python_version']}</code>\n"
            f"⚙️ <b>OS:</b> <code>{metrics['os_name']}</code>"
        )
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin:refresh")]])
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode=constants.ParseMode.HTML)

    elif data == "admin:logs":
        if not os.path.exists(LOG_FILE):
            await query.message.reply_text("ℹ️ Log file not created yet.")
            return
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-20:]
            content = html.escape("".join(lines).strip())
        text = (
            "<blockquote>📜 <b>Recent Log Entries</b></blockquote>\n\n"
            f"<pre><code class=\"language-log\">{content[-3000:]}</code></pre>"
        )
        back_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Send Full Log File", callback_data="admin:send_logfile")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin:refresh")],
        ])
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode=constants.ParseMode.HTML)

    elif data == "admin:send_logfile":
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "rb") as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename="bot.log",
                    caption="📜 <b>Bot Execution Log File</b>",
                    parse_mode=constants.ParseMode.HTML,
                )
        else:
            await query.message.reply_text("ℹ️ Log file not found.")

    elif data == "admin:broadcast_help":
        text = (
            "<blockquote>📢 <b>How to Broadcast:</b></blockquote>\n\n"
            "Use the command:\n"
            "<code>/broadcast &lt;Your message here&gt;</code>\n\n"
            "Or reply to any photo/audio/file with <code>/broadcast</code> to forward it to all users."
        )
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin:refresh")]])
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode=constants.ParseMode.HTML)

    elif data == "admin:clearcache":
        temp_root = tempfile.gettempdir()
        deleted = 0
        for item in os.listdir(temp_root):
            if item.startswith("spotibot_"):
                shutil.rmtree(os.path.join(temp_root, item), ignore_errors=True)
                deleted += 1
        text = f"<blockquote>🧹 <b>Cache Cleaned:</b> {deleted} temp folders removed.</blockquote>"
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin:refresh")]])
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode=constants.ParseMode.HTML)

    elif data == "admin:close":
        try:
            await query.message.delete()
        except Exception:
            pass
