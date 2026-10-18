import os
import re
import uuid
import html
import logging
import asyncio
import tempfile
import shutil
import gc
import random
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    constants,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from .spotify import SpotifyClient, parse_spotify_url
from .downloader import AudioDownloader
from .search import search_songs, get_album_tracks, get_related_tracks
from .models import CollectionInfo, TrackInfo
from .storage import BotDatabase, DATA_DIR
from .admin import (
    is_admin,
    admin_command,
    stats_command,
    broadcast_command,
    logs_command,
    sendlog_command,
    ban_command,
    unban_command,
    userinfo_command,
    clearcache_command,
    restart_command,
    cookies_command,
    admin_callback_router,
)

# Load environment variables
load_dotenv()

# Setup Dual Logging (Console + File)
os.makedirs(DATA_DIR, exist_ok=True)
log_file_path = os.path.join(DATA_DIR, "bot.log")
file_handler = RotatingFileHandler(
    log_file_path,
    maxBytes=5 * 1024 * 1024,
    backupCount=2,
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        file_handler,
    ]
)
logger = logging.getLogger(__name__)

# SQLite Database Instance
db = BotDatabase.get_instance()

def track_and_check_banned(update: Update) -> bool:
    """Records/updates user in SQLite and returns True if banned."""
    user = update.effective_user
    if not user:
        return False
    db.register_or_update_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    return db.is_banned(user.id)

# In-memory storage for search results & Spotify sessions (bounded, metadata-only)
CACHE: Dict[str, Any] = {}

def set_cache(key: str, val: Any):
    """Keep memory footprint tiny by bounding cache size and storing only metadata."""
    if len(CACHE) >= 200:
        oldest_key = next(iter(CACHE))
        CACHE.pop(oldest_key, None)
    CACHE[key] = val

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
AVATAR_PATH = os.path.join(ASSETS_DIR, "bot_avatar.jpg")
WELCOME_COVER_PATH = os.path.join(ASSETS_DIR, "welcome_cover.png")
DEFAULT_COVER = AVATAR_PATH if os.path.exists(AVATAR_PATH) else "https://i.scdn.co/image/ab67616d0000b273baf89eb11ec7c657805d2da0"

def esc(val: Any) -> str:
    """Escapes strings for safe Telegram HTML rendering."""
    return html.escape(str(val)) if val is not None else ""

def format_duration(duration_ms: int) -> str:
    if not duration_ms:
        return "--:--"
    total_seconds = int(duration_ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"

def format_file_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def get_user_settings(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    fmt = context.user_data.get("format", os.getenv("DEFAULT_AUDIO_FORMAT", "mp3"))
    bitrate = context.user_data.get("bitrate", os.getenv("DEFAULT_BITRATE", "320"))
    return fmt, bitrate

# Channel for storing downloaded songs as a permanent Telegram-backed CDN
CACHE_CHANNEL_ID = os.getenv("TELEGRAM_CACHE_CHANNEL_ID", "-1004474870702").strip()

def get_track_cache_keys(track: TrackInfo) -> list[str]:
    """Generates primary and secondary keys for matching cached tracks."""
    keys = []
    if track.spotify_id:
        keys.append(f"sp:{track.spotify_id.strip().lower()}")
    # Clean normalized string key: artist - title
    norm_key = re.sub(r'[^a-zA-Z0-9]', '', f"{track.artist_str}_{track.title}").lower()
    if norm_key:
        keys.append(f"norm:{norm_key}")
    return keys


def download_and_tag_worker(track: TrackInfo, temp_dir: str, audio_format: str, bitrate: str, progress_callback=None):
    downloader = AudioDownloader(
        output_dir=temp_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        skip_existing=False,
    )
    success, filepath, status = downloader.download_track(track=track, progress_callback=progress_callback)
    return success, filepath, status

# ----------------- Handlers -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if track_and_check_banned(update):
        await update.message.reply_text("⛔ <b>Access Restricted:</b> Your account is restricted by an administrator.", parse_mode=constants.ParseMode.HTML)
        return

    welcome_text = (
        "<blockquote>👋 <b>Welcome to Universal Songs Downloader!</b> 🎵</blockquote>\n\n"
        "Download studio-quality audio (up to <b>320kbps MP3 / FLAC</b>) "
        "with embedded ID3 tags and high-resolution cover artwork.\n\n"
        "<b>✨ How to Search:</b>\n"
        "• 🔍 <b>Exact Match:</b> <code>Song Name - Singer Name</code>\n"
        "   <i>e.g. <code>Mahamaya - Supun Perera</code> or <code>Blinding Lights - The Weeknd</code></i>\n"
        "• 🎶 <b>Spotify Track:</b> Paste any track link\n"
        "• 💿 <b>Spotify Album:</b> Paste any album link\n"
        "• 📜 <b>Spotify Playlist:</b> Paste any playlist link\n\n"
        "<blockquote>👇 <i>Use the buttons below to configure quality or get search tips:</i></blockquote>"
    )

    user_id = update.effective_user.id
    keyboard = [
        [
            InlineKeyboardButton("🔍 Search Hints", callback_data="ui:search_hint"),
            InlineKeyboardButton("⚙️ Audio Quality", callback_data="ui:settings"),
        ],
        [
            InlineKeyboardButton("ℹ️ How to Use", callback_data="ui:help"),
        ]
    ]

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🛡️ Admin Dashboard", callback_data="admin:refresh")])

    welcome_src = WELCOME_COVER_PATH if os.path.exists(WELCOME_COVER_PATH) else (AVATAR_PATH if os.path.exists(AVATAR_PATH) else DEFAULT_COVER)
    photo_obj = open(welcome_src, "rb") if (isinstance(welcome_src, str) and os.path.exists(welcome_src)) else welcome_src
    try:
        await update.message.reply_photo(
            photo=photo_obj,
            caption=welcome_text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        if hasattr(photo_obj, "close"):
            photo_obj.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if track_and_check_banned(update):
        return

    help_text = (
        "<blockquote>📖 <b>Universal Songs Downloader Guide</b></blockquote>\n\n"
        "<b>1️⃣ Search Exact Songs:</b>\n"
        "For the best and most accurate results, search in this format:\n"
        "👉 <code>Song Name - Singer Name</code>\n\n"
        "<i>Examples:</i>\n"
        "• <code>Mahamaya - Supun Perera</code>\n"
        "• <code>Blinding Lights - The Weeknd</code>\n"
        "• <code>Shape of You - Ed Sheeran</code>\n"
        "• <code>Manike Mage Hithe - Yohani</code>\n\n"
        "<blockquote>💡 <i>Tip: Adding the singer's name guarantees 100% exact match and original studio quality!</i></blockquote>\n\n"
        "<b>2️⃣ Download from Spotify:</b>\n"
        "Paste any Spotify URL directly:\n"
        "• <code>https://open.spotify.com/track/...</code>\n"
        "• <code>https://open.spotify.com/album/...</code>\n"
        "• <code>https://open.spotify.com/playlist/...</code>\n\n"
        "<b>3️⃣ Audio Quality:</b>\n"
        "Default quality is <b>320kbps MP3</b>. You can customize this to FLAC, M4A, or 192kbps in <b>/settings</b>.\n\n"
        "<blockquote>⚡ <i>All files include embedded cover art, artist, and album tags!</i></blockquote>"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(help_text, parse_mode=constants.ParseMode.HTML)
    else:
        await update.message.reply_text(help_text, parse_mode=constants.ParseMode.HTML)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if track_and_check_banned(update):
        return

    fmt, bitrate = get_user_settings(context)
    text = (
        "<blockquote>⚙️ <b>Audio Quality Settings</b></blockquote>\n\n"
        f"• Current Format: <b>{fmt.upper()}</b>\n"
        f"• Current Bitrate: <b>{bitrate} kbps</b>\n\n"
        "<blockquote>👇 <i>Select your preferred audio format:</i></blockquote>"
    )
    keyboard = [
        [
            InlineKeyboardButton(f"{'✅ ' if fmt=='mp3' and bitrate=='320' else ''}MP3 (320 kbps)", callback_data="set:mp3:320"),
            InlineKeyboardButton(f"{'✅ ' if fmt=='mp3' and bitrate=='192' else ''}MP3 (192 kbps)", callback_data="set:mp3:192"),
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if fmt=='m4a' else ''}M4A (AAC)", callback_data="set:m4a:256"),
            InlineKeyboardButton(f"{'✅ ' if fmt=='flac' else ''}FLAC (Lossless)", callback_data="set:flac:0"),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="ui:back_start"),
        ]
    ]
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_caption(
                caption=text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text=text,
                parse_mode=constants.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    else:
        await update.message.reply_text(text, parse_mode=constants.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if track_and_check_banned(update):
        await update.message.reply_text("⛔ <b>Access Restricted:</b> Your account is restricted by an administrator.", parse_mode=constants.ParseMode.HTML)
        return

    if not update.message or not update.message.text:
        # Log forwarded channel info if present (useful for getting channel ID)
        if update.message and update.message.forward_from_chat:
            fc = update.message.forward_from_chat
            logger.info(f"Forwarded from chat: id={fc.id} title={fc.title} type={fc.type}")
        return

    text = update.message.text.strip()
    if not text:
        return

    parsed = parse_spotify_url(text)
    if parsed:
        await handle_spotify_url(update, context, text)
    else:
        await handle_song_search(update, context, text)

async def handle_song_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    status_msg = await update.message.reply_text(
        f"🔍 <i>Searching for <b>{esc(query)}</b>...</i>",
        parse_mode=constants.ParseMode.HTML
    )

    results = await asyncio.to_thread(search_songs, query, 5)

    if not results:
        await status_msg.edit_text(
            f"❌ <b>No songs found for:</b> <i>{esc(query)}</i>\n\n"
            "<blockquote>💡 <b>Tip:</b> Try the <code>Song Name - Singer Name</code> format for exact results.\n"
            f"e.g. <code>{esc(query)} - Artist Name</code></blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    await status_msg.delete()

    cache_id = uuid.uuid4().hex[:8]
    set_cache(cache_id, {
        "type": "search",
        "query": query,
        "tracks": results,
    })

    # Format beautiful caption with search results list
    caption_lines = [
        f"<blockquote>🔍 <b>Search Results for:</b> <i>{esc(query)}</i></blockquote>\n"
    ]
    for idx, track in enumerate(results, start=1):
        dur = format_duration(track.duration_ms)
        caption_lines.append(f"<b>{idx}.</b> {esc(track.title)} — <i>{esc(track.artist_str)}</i> <code>[{dur}]</code>")

    caption_lines.append("\n<blockquote>👇 <b>Tap a song below to download</b>  •  <i>Tip: Search <code>Song - Singer</code> for exact match!</i></blockquote>")
    caption_text = "\n".join(caption_lines)

    keyboard = []
    for idx, track in enumerate(results, start=1):
        dur = format_duration(track.duration_ms)
        dur_tag = f"[{dur}]"
        raw_info = f"{idx}. {track.title} — {track.artist_str}"
        max_len = 44 - len(dur_tag)
        if len(raw_info) > max_len:
            btn_text = f"{raw_info[:max_len-1]}… {dur_tag}"
        else:
            btn_text = f"{raw_info} {dur_tag}"

        btn = InlineKeyboardButton(f"🎵 {btn_text}", callback_data=f"dl:s:{cache_id}:{idx-1}")
        keyboard.append([btn])

    action_row = [
        InlineKeyboardButton("⬇️ Download All (1-5)", callback_data=f"dl:s:{cache_id}:all"),
        InlineKeyboardButton("❌ Dismiss", callback_data="ui:dismiss"),
    ]
    keyboard.append(action_row)

    cover_photo = results[0].cover_url or DEFAULT_COVER

    try:
        await update.message.reply_photo(
            photo=cover_photo,
            caption=caption_text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        await update.message.reply_text(
            text=caption_text,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def handle_spotify_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    status_msg = await update.message.reply_text("🔄 <i>Resolving Spotify metadata...</i>", parse_mode=constants.ParseMode.HTML)

    spotify = SpotifyClient()
    try:
        collection: CollectionInfo = await asyncio.to_thread(spotify.fetch, url)
    except Exception as e:
        await status_msg.edit_text(f"❌ <i>Could not load Spotify link: {esc(e)}</i>", parse_mode=constants.ParseMode.HTML)
        return

    await status_msg.delete()

    cache_id = uuid.uuid4().hex[:8]
    set_cache(cache_id, {
        "type": collection.type,
        "collection": collection,
    })

    cover = collection.cover_url or DEFAULT_COVER

    if collection.type == "track":
        track = collection.tracks[0]
        caption = (
            f"<blockquote>🎵 <b>{esc(track.title)}</b></blockquote>\n\n"
            f"👤 <b>Artist:</b> {esc(track.artist_str)}\n"
            f"💿 <b>Album:</b> {esc(track.album)}\n"
            f"⏱ <b>Duration:</b> {format_duration(track.duration_ms)}\n"
            f"📅 <b>Released:</b> {esc(track.release_date or 'N/A')}\n\n"
            f"<blockquote>👇 <i>Select format to download:</i></blockquote>"
        )
        keyboard = [
            [
                InlineKeyboardButton("⬇️ Download MP3 (320kbps)", callback_data=f"dl:t:{cache_id}:mp3"),
                InlineKeyboardButton("⬇️ FLAC", callback_data=f"dl:t:{cache_id}:flac"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="ui:dismiss"),
            ]
        ]
    elif collection.type in ("album", "playlist", "artist"):
        type_name = collection.type.capitalize()
        caption = (
            f"<blockquote>💿 <b>{esc(type_name)}: {esc(collection.title)}</b></blockquote>\n\n"
            f"🔢 <b>Total Tracks:</b> {collection.total_count} songs\n"
            f"ℹ️ {esc(collection.description)}\n\n"
            f"<blockquote>👇 <i>Download full collection or view tracklist:</i></blockquote>"
        )
        keyboard = [
            [
                InlineKeyboardButton(f"⬇️ Download All ({collection.total_count} songs)", callback_data=f"dl:c:{cache_id}:all"),
            ],
            [
                InlineKeyboardButton("📋 View Tracklist", callback_data=f"view:{cache_id}:0"),
                InlineKeyboardButton("❌ Cancel", callback_data="ui:dismiss"),
            ]
        ]
    else:
        caption = f"🎵 <b>{esc(collection.title)}</b>"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="ui:dismiss")]]

    try:
        await update.message.reply_photo(
            photo=cover,
            caption=caption,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        await update.message.reply_text(
            text=caption,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# ----------------- Callback Handlers -----------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if track_and_check_banned(update):
        await update.callback_query.answer("⛔ Access restricted by administrator.", show_alert=True)
        return

    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("admin:"):
        await admin_callback_router(update, context)
        return

    if data == "ui:dismiss":
        await query.message.delete()
        return

    if data == "ui:help":
        await help_command(update, context)
        return

    if data == "ui:settings":
        await settings_command(update, context)
        return

    if data == "ui:back_start":
        await start_command(update, context)
        return

    if data == "ui:search_hint":
        await query.message.reply_text(
            "<blockquote>🔍 <b>How to Search for Exact Songs</b></blockquote>\n\n"
            "For the most accurate results, use this format:\n\n"
            "👉 <code>Song Name - Singer Name</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>Mahamaya - Supun Perera</code>\n"
            "• <code>Blinding Lights - The Weeknd</code>\n"
            "• <code>Shape of You - Ed Sheeran</code>\n"
            "• <code>Manike Mage Hithe - Yohani</code>\n"
            "• <code>Serandib - Bathiya & Santhush</code>\n\n"
            "<blockquote>💡 <i>Just the song name also works, but adding the singer gives you the exact match!</i></blockquote>\n\n"
            "🎶 <b>Download from Spotify:</b>\n"
            "Paste a Spotify link to download a track, album, or playlist directly.",
            parse_mode=constants.ParseMode.HTML
        )
        return

    if data.startswith("set:"):
        _, fmt, bitrate = data.split(":")
        context.user_data["format"] = fmt
        context.user_data["bitrate"] = bitrate
        await settings_command(update, context)
        return

    # View tracklist pagination
    if data.startswith("view:"):
        _, cache_id, page_str = data.split(":")
        page = int(page_str)
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ <i>Session expired. Please paste the link again.</i>", parse_mode=constants.ParseMode.HTML)
            return

        collection: CollectionInfo = cached["collection"]
        tracks = collection.tracks
        per_page = 10
        total_pages = (len(tracks) + per_page - 1) // per_page
        start_idx = page * per_page
        end_idx = min(start_idx + per_page, len(tracks))

        lines = [f"<blockquote>📋 <b>Tracklist: {esc(collection.title)}</b> (Page {page+1}/{total_pages})</blockquote>\n"]
        for i in range(start_idx, end_idx):
            t = tracks[i]
            dur = format_duration(t.duration_ms)
            lines.append(f"<b>{i+1}.</b> {esc(t.title)} — <i>{esc(t.artist_str)}</i> <code>[{dur}]</code>")

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view:{cache_id}:{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"view:{cache_id}:{page+1}"))

        keyboard = []
        if nav_buttons:
            keyboard.append(nav_buttons)
        keyboard.append([
            InlineKeyboardButton(f"⬇️ Download All ({len(tracks)})", callback_data=f"dl:c:{cache_id}:all"),
            InlineKeyboardButton("🔙 Close", callback_data="ui:dismiss"),
        ])

        await query.message.reply_text(
            text="\n".join(lines),
            parse_mode=constants.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Download track from Spotify
    if data.startswith("dl:t:"):
        _, _, cache_id, chosen_fmt = data.split(":")
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ <i>Session expired. Please send the link again.</i>", parse_mode=constants.ParseMode.HTML)
            return

        track: TrackInfo = cached["collection"].tracks[0]
        _, user_bitrate = get_user_settings(context)
        await send_track_to_user(
            chat_id=query.message.chat_id,
            track=track,
            context=context,
            audio_format=chosen_fmt,
            bitrate=user_bitrate,
        )
        return

    # Download from search result
    if data.startswith("dl:s:"):
        _, _, cache_id, item = data.split(":")
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ <i>Session expired. Please search again.</i>", parse_mode=constants.ParseMode.HTML)
            return

        tracks = cached["tracks"]
        user_fmt, user_bitrate = get_user_settings(context)

        if item == "all":
            for track in tracks:
                await send_track_to_user(
                    chat_id=query.message.chat_id,
                    track=track,
                    context=context,
                    audio_format=user_fmt,
                    bitrate=user_bitrate,
                )
        else:
            idx = int(item)
            track = tracks[idx]
            await send_track_to_user(
                chat_id=query.message.chat_id,
                track=track,
                context=context,
                audio_format=user_fmt,
                bitrate=user_bitrate,
            )
        return

    # Download collection
    if data.startswith("dl:c:"):
        _, _, cache_id, _ = data.split(":")
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ <i>Session expired. Please paste the link again.</i>", parse_mode=constants.ParseMode.HTML)
            return

        collection: CollectionInfo = cached["collection"]
        user_fmt, user_bitrate = get_user_settings(context)
        total = collection.total_count

        status_msg = await query.message.reply_text(
            f"🚀 <i>Starting batch download for <b>{esc(collection.title)}</b> ({total} songs)...</i>",
            parse_mode=constants.ParseMode.HTML,
        )

        for idx, track in enumerate(collection.tracks, start=1):
            await status_msg.edit_text(
                f"⬇️ <i>Downloading ({idx}/{total}): <b>{esc(track.title)}</b>...</i>",
                parse_mode=constants.ParseMode.HTML,
            )
            await send_track_to_user(
                chat_id=query.message.chat_id,
                track=track,
                context=context,
                audio_format=user_fmt,
                bitrate=user_bitrate,
            )

        await status_msg.edit_text(
            f"✅ <b>Completed downloading all {total} songs for:</b> {esc(collection.title)}",
            parse_mode=constants.ParseMode.HTML,
        )
        return

# ----------------- Download & Sender Helper -----------------

STATUS_ING_PHRASES = [
    "Resolving high-quality audio stream...",
    "Finding optimal bitrate candidates...",
    "Extracting audio stream metadata...",
    "Establishing high-speed audio pipeline...",
    "Matching track duration signatures...",
    "Analyzing audio frequencies & bitrates...",
    "Buffering initial audio chunks...",
    "Fetching studio master audio...",
    "Streaming high-bitrate audio packets...",
    "Synchronizing audio stream...",
    "Tuning audio pipeline...",
    "Receiving audio segments...",
]

def render_standalone_progress(state: dict) -> str:
    status = state.get("status", "downloading")
    percent = max(0.0, min(state.get("percent", 0.0), 100.0))
    action_text = state.get("action_text", "Resolving high-quality audio stream...")

    # Prominent 16-block progress bar
    total_blocks = 16
    filled = int((percent / 100.0) * total_blocks)
    bar = "▰" * filled + "▱" * (total_blocks - filled)

    if status == "downloading":
        speed = state.get("speed", 0.0)
        speed_str = f"{speed / (1024 * 1024):.1f} MB/s" if speed >= 1024 * 1024 else (f"{speed / 1024:.0f} KB/s" if speed > 0 else "-- KB/s")
        downloaded_mb = state.get("downloaded", 0) / (1024 * 1024)
        total_mb = state.get("total", 0) / (1024 * 1024)
        eta = state.get("eta", 0)
        eta_str = f"{eta // 60:02d}:{eta % 60:02d}" if eta and eta > 0 else "--:--"
        size_str = f"{downloaded_mb:.1f} / {total_mb:.1f} MB" if total_mb > 0 else f"{downloaded_mb:.1f} MB"

        return (
            f"<blockquote>"
            f"⬇️ <b>Downloading Audio...</b> <b>{percent:.1f}%</b>\n"
            f"<code>[{bar}]</code>\n\n"
            f"⚡ <b>Speed:</b> <code>{speed_str}</code>   •   ⏱ <b>ETA:</b> <code>{eta_str}</code>\n"
            f"📦 <b>Size:</b> <code>{size_str}</code>\n\n"
            f"<i>{esc(action_text)}</i>"
            f"</blockquote>"
        )
    elif status == "converting":
        full_bar = "▰" * 15 + "▱"
        return (
            f"<blockquote>"
            f"🎚️ <b>Converting & Tagging...</b> <b>95.0%</b>\n"
            f"<code>[{full_bar}]</code>\n\n"
            f"✨ <i>{esc(action_text)}</i>"
            f"</blockquote>"
        )
    elif status == "uploading":
        full_bar = "▰" * 16
        return (
            f"<blockquote>"
            f"📤 <b>Uploading to Telegram...</b> <b>100%</b>\n"
            f"<code>[{full_bar}]</code>\n\n"
            f"🚀 <i>{esc(action_text)}</i>"
            f"</blockquote>"
        )
    return f"<blockquote>⏳ <i>{esc(action_text)}</i></blockquote>"

async def send_track_to_user(
    chat_id: int,
    track: TrackInfo,
    context: ContextTypes.DEFAULT_TYPE,
    audio_format: str = "mp3",
    bitrate: str = "320",
):
    temp_dir = tempfile.mkdtemp(prefix="spotibot_")
    current_action = random.choice(STATUS_ING_PHRASES)
    progress_state = {
        "status": "downloading",
        "action_text": current_action,
        "percent": 0.0,
        "downloaded": 0,
        "total": 0,
        "speed": 0.0,
        "eta": 0,
    }

    dur_str = format_duration(track.duration_ms)
    album_display = track.album if track.album and track.album not in ("Single", "Unknown Album") else "Single"
    cover_photo = track.cover_url or DEFAULT_COVER

    # 1. Send permanent photo card with thumbnail & song details
    card_caption = (
        f"🎵 <b>{esc(track.title)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Artist:</b> {esc(track.artist_str)}\n"
        f"💿 <b>Album:</b> {esc(album_display)}\n"
        f"⏱ <b>Duration:</b> {dur_str}  •  <b>Quality:</b> {audio_format.upper()} ({bitrate}k)\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=cover_photo,
            caption=card_caption,
            parse_mode=constants.ParseMode.HTML,
        )
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id,
            text=card_caption,
            parse_mode=constants.ParseMode.HTML,
        )

    # --- TELEGRAM CDN CACHE CHECK ---
    cache_keys = get_track_cache_keys(track)
    cached_file_id = None
    for k in cache_keys:
        cid = db.get_cached_file_id(k)
        if cid:
            cached_file_id = cid
            break

    bot_username = context.bot.username or "UniversalSongsDownloader"
    caption_text = f"🎵 <b>{esc(track.title)}</b> — <i>{esc(track.artist_str)}</i>\n⚡ @{bot_username}"

    if cached_file_id:
        logger.info(f"⚡ CDN CACHE HIT for {track.query} (file_id: {cached_file_id[:15]}...)")
        try:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=cached_file_id,
                caption=caption_text,
                parse_mode=constants.ParseMode.HTML,
            )
            db.record_download(user_id=chat_id)
            asyncio.create_task(send_album_and_playlist_suggestions(chat_id, track, context))
            shutil.rmtree(temp_dir, ignore_errors=True)
            return
        except Exception as cache_err:
            logger.warning(f"Failed to send cached file_id, will download fresh: {cache_err}")

    # 2. Send temporary progress message directly below the card
    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=render_standalone_progress(progress_state),
        parse_mode=constants.ParseMode.HTML,
    )

    def ytdl_progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            speed = d.get("speed") or 0.0
            eta = d.get("eta") or 0
            if total > 0:
                pct = min(99.0, (downloaded / total) * 100.0)
                progress_state["percent"] = pct
                if pct < 35:
                    progress_state["action_text"] = "Streaming high-bitrate audio packets..."
                elif pct < 75:
                    progress_state["action_text"] = "Downloading audio stream segments..."
                else:
                    progress_state["action_text"] = "Finalizing audio stream transfer..."
            else:
                progress_state["action_text"] = "Streaming studio master audio..."

            progress_state["downloaded"] = downloaded
            progress_state["total"] = total
            progress_state["speed"] = speed
            progress_state["eta"] = eta
            progress_state["status"] = "downloading"
        elif d.get("status") == "finished":
            progress_state["status"] = "converting"
            progress_state["action_text"] = "Embedding HD cover artwork & ID3 metadata..."
            progress_state["percent"] = 95.0

    stop_event = asyncio.Event()

    async def live_progress_reporter():
        nonlocal current_action
        last_card = ""
        while not stop_event.is_set():
            try:
                # If still waiting for initial downloaded bytes, randomly pick an "-ing" phrase
                if progress_state["status"] == "downloading" and progress_state["downloaded"] == 0:
                    choices = [p for p in STATUS_ING_PHRASES if p != current_action]
                    current_action = random.choice(choices) if choices else random.choice(STATUS_ING_PHRASES)
                    progress_state["action_text"] = current_action

                card = render_standalone_progress(progress_state)
                if card != last_card:
                    await progress_msg.edit_text(text=card, parse_mode=constants.ParseMode.HTML)
                    last_card = card
                await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.UPLOAD_VOICE)
            except Exception:
                pass
            await asyncio.sleep(1.4)

    reporter_task = asyncio.create_task(live_progress_reporter())

    try:
        # Download in worker thread with progress hook
        success, filepath, status = await asyncio.to_thread(
            download_and_tag_worker,
            track,
            temp_dir,
            audio_format,
            bitrate,
            ytdl_progress_hook,
        )
    finally:
        stop_event.set()
        reporter_task.cancel()

    try:
        if not success or not os.path.exists(filepath):
            err_msg = f"❌ <b>Failed to download {esc(track.title)}:</b>\n<i>{esc(status)}</i>"
            await progress_msg.edit_text(text=err_msg, parse_mode=constants.ParseMode.HTML)
            return

        # Prepare thumbnail bytes
        thumb_bytes = None
        if track.cover_url:
            try:
                r = requests.get(track.cover_url, timeout=5)
                if r.status_code == 200:
                    thumb_bytes = r.content
            except Exception:
                pass

        # Update progress message to uploading phase (100%)
        progress_state["status"] = "uploading"
        progress_state["action_text"] = "Delivering high-fidelity audio to chat..."
        progress_state["percent"] = 100.0
        try:
            await progress_msg.edit_text(
                text=render_standalone_progress(progress_state),
                parse_mode=constants.ParseMode.HTML
            )
        except Exception:
            pass

        # Deliver audio file directly under the card
        duration_sec = int(track.duration_ms / 1000) if track.duration_ms else None

        sent_audio_msg = None
        with open(filepath, "rb") as audio_file:
            sent_audio_msg = await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=track.title,
                performer=track.artist_str,
                duration=duration_sec,
                thumbnail=thumb_bytes,
                caption=caption_text,
                parse_mode=constants.ParseMode.HTML,
                read_timeout=300,
                write_timeout=300,
                connect_timeout=60,
            )

        # Extract file_id from delivered audio message
        file_id = None
        if sent_audio_msg and sent_audio_msg.audio:
            file_id = sent_audio_msg.audio.file_id

        # Cache file_id and post to database channel for safe keeping
        if file_id:
            for k in cache_keys:
                db.set_cached_file_id(k, file_id, track.title, track.artist_str)
            logger.info(f"Saved {track.query} file_id to local SQLite cache.")

            # Forward / send to the backup database channel
            if CACHE_CHANNEL_ID:
                try:
                    channel_target = int(CACHE_CHANNEL_ID) if CACHE_CHANNEL_ID.lstrip("-").isdigit() else CACHE_CHANNEL_ID
                    await context.bot.send_audio(
                        chat_id=channel_target,
                        audio=file_id,
                        title=track.title,
                        performer=track.artist_str,
                        caption=f"🎵 {track.title} - {track.artist_str}\n🆔 Keys: {', '.join(cache_keys)}",
                    )
                except Exception as ch_err:
                    logger.warning(f"Could not post copy to storage channel {CACHE_CHANNEL_ID}: {ch_err}")

        # Record download in SQLite
        db.record_download(user_id=chat_id)

        # Delete the temporary progress message so chat remains clean!
        try:
            await progress_msg.delete()
        except Exception:
            pass

        # Suggest full album and related playlist if available
        asyncio.create_task(send_album_and_playlist_suggestions(chat_id, track, context))

    except Exception as e:
        logger.error(f"Error sending track {track.title}: {e}")
        try:
            await progress_msg.edit_text(f"⚠️ <i>Error processing {esc(track.title)}: {esc(e)}</i>", parse_mode=constants.ParseMode.HTML)
        except Exception:
            pass
    finally:
        # Clean up temporary directory and free buffers
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()

async def send_album_and_playlist_suggestions(chat_id: int, track: TrackInfo, context: ContextTypes.DEFAULT_TYPE):
    """
    Suggests the full album (if part of an album) and related playlist/songs by the artist.
    """
    try:
        album_title = ""
        album_tracks = []
        album_cover = ""

        # 1. Fetch album tracks if album_id is available
        if track.album_id:
            album_title, album_cover, all_album_tracks = await asyncio.to_thread(get_album_tracks, track.album_id)
            norm_title = track.title.lower().strip()
            album_tracks = [t for t in all_album_tracks if norm_title not in t.title.lower() and t.title.lower() not in norm_title]

        # 2. Fetch related songs / playlist by artist
        related_tracks = await asyncio.to_thread(get_related_tracks, track.artist_str, track.title, 4)

        if not album_tracks and not related_tracks:
            return

        lines = ["<blockquote>✨ <b>More Music You Might Like</b></blockquote>\n"]
        keyboard = []

        # If album has other tracks
        if album_tracks and len(album_tracks) >= 1:
            clean_album_name = album_title or track.album
            lines.append(f"💿 <b>More from Album:</b> <i>{esc(clean_album_name)}</i>")
            for t in album_tracks[:3]:
                dur = format_duration(t.duration_ms)
                lines.append(f"• {esc(t.title)} <code>[{dur}]</code>")
            if len(album_tracks) > 3:
                lines.append(f"<i>...and {len(album_tracks) - 3} more tracks</i>")
            lines.append("")

            # Cache album collection for one-tap download
            album_cache_id = uuid.uuid4().hex[:8]
            album_collection = CollectionInfo(
                type="album",
                title=clean_album_name,
                cover_url=album_cover or track.cover_url,
                tracks=album_tracks,
            )
            set_cache(album_cache_id, {"type": "album", "collection": album_collection})
            keyboard.append([
                InlineKeyboardButton(f"💿 Download Full Album ({len(album_tracks)} songs)", callback_data=f"dl:c:{album_cache_id}:all")
            ])

        # If related recommendations exist
        if related_tracks:
            lines.append(f"🎶 <b>Related Playlist by {esc(track.artist_str)}:</b>")
            for t in related_tracks[:3]:
                dur = format_duration(t.duration_ms)
                lines.append(f"• {esc(t.title)} <code>[{dur}]</code>")
            lines.append("")

            # Cache related playlist for one-tap download
            mix_cache_id = uuid.uuid4().hex[:8]
            mix_collection = CollectionInfo(
                type="playlist",
                title=f"Best of {track.artist_str}",
                cover_url=track.cover_url,
                tracks=related_tracks,
            )
            set_cache(mix_cache_id, {"type": "playlist", "collection": mix_collection})
            keyboard.append([
                InlineKeyboardButton(f"🎧 Download Related Mix ({len(related_tracks)} songs)", callback_data=f"dl:c:{mix_cache_id}:all")
            ])

        keyboard.append([InlineKeyboardButton("❌ Dismiss", callback_data="ui:dismiss")])

        suggestion_text = "\n".join(lines).strip()
        await context.bot.send_message(
            chat_id=chat_id,
            text=suggestion_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=constants.ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Error sending album/playlist suggestions: {e}")

async def handle_document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    doc = update.message.document
    if not doc or not doc.file_name:
        return

    name_lower = doc.file_name.lower()
    if "cookie" in name_lower or name_lower.endswith(".txt"):
        try:
            target_path = os.path.join(DATA_DIR, "cookies.txt")
            tg_file = await context.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(target_path)
            size_kb = os.path.getsize(target_path) / 1024
            await update.message.reply_text(
                f"✅ <b>YouTube Cookies Installed!</b>\n\n"
                f"📁 Saved to: <code>data/cookies.txt</code>\n"
                f"📦 Size: <code>{size_kb:.1f} KB</code>\n\n"
                f"🚀 <i>Datacenter IP bot-challenge bypass is now active!</i>",
                parse_mode=constants.ParseMode.HTML,
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Error saving cookies: {esc(e)}")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment!")
        sys.exit(1)

    application = (
        Application.builder()
        .token(token)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )

    # User Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))

    # Admin Commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("bc", broadcast_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("log", logs_command))
    application.add_handler(CommandHandler("sendlog", sendlog_command))
    application.add_handler(CommandHandler("cookies", cookies_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("userinfo", userinfo_command))
    application.add_handler(CommandHandler("clearcache", clearcache_command))
    application.add_handler(CommandHandler("restart", restart_command))

    # Callback & Message Handlers
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Universal Songs Downloader Telegram Bot starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
