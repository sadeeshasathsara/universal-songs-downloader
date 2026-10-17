import os
import uuid
import logging
import asyncio
import tempfile
import shutil
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
from .search import search_songs
from .models import CollectionInfo, TrackInfo

# Load environment variables
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# In-memory storage for search results & Spotify sessions
# Maps short UUID -> cached data
CACHE: Dict[str, Any] = {}

DEFAULT_COVER = "https://cdn-icons-png.flaticon.com/512/3844/3844724.png"

def format_duration(duration_ms: int) -> str:
    if not duration_ms:
        return "--:--"
    total_seconds = int(duration_ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"

def get_user_settings(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    fmt = context.user_data.get("format", os.getenv("DEFAULT_AUDIO_FORMAT", "mp3"))
    bitrate = context.user_data.get("bitrate", os.getenv("DEFAULT_BITRATE", "320"))
    return fmt, bitrate

def download_and_tag_worker(track: TrackInfo, temp_dir: str, audio_format: str, bitrate: str):
    downloader = AudioDownloader(
        output_dir=temp_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        skip_existing=False,
    )
    success, filepath, status = downloader.download_track(track=track)
    return success, filepath, status

# ----------------- Handlers -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **Welcome to Universal Songs Downloader!** 🎵\n\n"
        "I can download and send high-fidelity audio (up to 320kbps MP3 / FLAC) "
        "with complete ID3 tags and HD album artwork.\n\n"
        "✨ **What you can do:**\n"
        "• 🔍 **Search Songs**: Simply type any song or artist name (e.g. `Blinding Lights`)\n"
        "• 🎶 **Spotify Track**: Paste any track link\n"
        "• 💿 **Spotify Album**: Paste any album link\n"
        "• 📜 **Spotify Playlist**: Paste any playlist link\n\n"
        "Use the buttons below to configure quality or get help:"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔍 Search Songs", callback_data="ui:search_hint"),
            InlineKeyboardButton("⚙️ Audio Quality", callback_data="ui:settings"),
        ],
        [
            InlineKeyboardButton("ℹ️ How to Use", callback_data="ui:help"),
        ]
    ]

    avatar_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "bot_avatar.jpg")
    photo_obj = open(avatar_path, "rb") if os.path.exists(avatar_path) else DEFAULT_COVER

    try:
        await update.message.reply_photo(
            photo=photo_obj,
            caption=welcome_text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        await update.message.reply_photo(
            photo=DEFAULT_COVER,
            caption=welcome_text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    finally:
        if hasattr(photo_obj, "close"):
            photo_obj.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **How to Use Universal Songs Downloader**\n\n"
        "1️⃣ **Song Search:**\n"
        "Just type a song name, e.g. `Coldplay Viva La Vida` or `Adele Hello`.\n"
        "The bot will display the top matching songs with thumbnails and download buttons.\n\n"
        "2️⃣ **Spotify Links:**\n"
        "Paste any link directly into the chat:\n"
        "• `https://open.spotify.com/track/...`\n"
        "• `https://open.spotify.com/album/...`\n"
        "• `https://open.spotify.com/playlist/...`\n\n"
        "3️⃣ **Audio Quality:**\n"
        "Default quality is **320kbps MP3**. You can switch to FLAC, M4A, or 192kbps in Settings.\n\n"
        "⚡ All downloaded files include embedded artist, album, track number, and cover art!"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(help_text, parse_mode=constants.ParseMode.MARKDOWN)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fmt, bitrate = get_user_settings(context)
    text = (
        f"⚙️ **Audio Quality Settings**\n\n"
        f"Current Format: **{fmt.upper()}**\n"
        f"Current Bitrate: **{bitrate} kbps**\n\n"
        "Select your preferred audio output format:"
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
        await update.callback_query.edit_message_caption(
            caption=text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    # Check if text is a Spotify URL
    parsed = parse_spotify_url(text)
    if parsed:
        await handle_spotify_url(update, context, text)
    else:
        await handle_song_search(update, context, text)

async def handle_song_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    status_msg = await update.message.reply_text(f"🔍 Searching for **{query}**...", parse_mode=constants.ParseMode.MARKDOWN)

    # Perform search asynchronously
    results = await asyncio.to_thread(search_songs, query, 5)

    if not results:
        await status_msg.edit_text(f"❌ No songs found for: *{query}*.\nTry searching with artist name.", parse_mode=constants.ParseMode.MARKDOWN)
        return

    # Delete status message
    await status_msg.delete()

    # Cache results
    cache_id = uuid.uuid4().hex[:8]
    CACHE[cache_id] = {
        "type": "search",
        "query": query,
        "tracks": results,
    }

    # Format caption
    caption_lines = [
        f"🎵 **Search Results for:** _{query}_\n",
        "👇 **Tap a song below to download:**\n",
    ]
    keyboard = []

    for idx, track in enumerate(results, start=1):
        dur = format_duration(track.duration_ms)
        caption_lines.append(f"**{idx}.** {track.title} — _{track.artist_str}_ `[{dur}]`")

        # Format button text with title, artist, and duration
        dur_tag = f"[{dur}]"
        raw_info = f"{idx}. {track.title} — {track.artist_str}"
        max_len = 45 - len(dur_tag)
        if len(raw_info) > max_len:
            btn_text = f"{raw_info[:max_len-1]}… {dur_tag}"
        else:
            btn_text = f"{raw_info} {dur_tag}"

        btn = InlineKeyboardButton(f"🎵 {btn_text}", callback_data=f"dl:s:{cache_id}:{idx-1}")
        keyboard.append([btn])

    # Add extra action row
    action_row = [
        InlineKeyboardButton("⬇️ Download All", callback_data=f"dl:s:{cache_id}:all"),
        InlineKeyboardButton("❌ Dismiss", callback_data="ui:dismiss"),
    ]
    keyboard.append(action_row)

    cover_photo = results[0].cover_url or DEFAULT_COVER
    caption_text = "\n".join(caption_lines)

    try:
        await update.message.reply_photo(
            photo=cover_photo,
            caption=caption_text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        # Fallback to plain text if photo URL has issue
        await update.message.reply_text(
            text=caption_text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def handle_spotify_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    status_msg = await update.message.reply_text("🔄 Resolving Spotify metadata...")

    spotify = SpotifyClient()
    try:
        collection: CollectionInfo = await asyncio.to_thread(spotify.fetch, url)
    except Exception as e:
        await status_msg.edit_text(f"❌ Could not load Spotify link: {e}")
        return

    await status_msg.delete()

    cache_id = uuid.uuid4().hex[:8]
    CACHE[cache_id] = {
        "type": collection.type,
        "collection": collection,
    }

    cover = collection.cover_url or DEFAULT_COVER

    if collection.type == "track":
        track = collection.tracks[0]
        caption = (
            f"🎵 **{track.title}**\n"
            f"👤 **Artist:** {track.artist_str}\n"
            f"💿 **Album:** {track.album}\n"
            f"⏱️ **Duration:** {format_duration(track.duration_ms)}\n"
            f"📅 **Released:** {track.release_date or 'N/A'}"
        )
        keyboard = [
            [
                InlineKeyboardButton("⬇️ Download (320kbps MP3)", callback_data=f"dl:t:{cache_id}:mp3"),
                InlineKeyboardButton("⬇️ FLAC", callback_data=f"dl:t:{cache_id}:flac"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="ui:dismiss"),
            ]
        ]
    elif collection.type in ("album", "playlist", "artist"):
        type_name = collection.type.capitalize()
        caption = (
            f"💿 **{type_name}: {collection.title}**\n"
            f"🔢 **Total Tracks:** {collection.total_count} songs\n"
            f"ℹ️ {collection.description}"
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
        caption = f"🎵 {collection.title}"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="ui:dismiss")]]

    try:
        await update.message.reply_photo(
            photo=cover,
            caption=caption,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        await update.message.reply_text(
            text=caption,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# ----------------- Callback Handlers -----------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "ui:dismiss":
        await query.message.delete()
        return

    if data == "ui:help":
        await help_command(update, context)
        return

    if data == "ui:settings":
        await settings_command(update, context)
        return

    if data == "ui:search_hint":
        await query.message.reply_text(
            "💡 Simply type any song title or artist name into the chat and send it!\n"
            "Example: `The Weeknd Blinding Lights`"
        )
        return

    if data.startswith("set:"):
        _, fmt, bitrate = data.split(":")
        context.user_data["format"] = fmt
        context.user_data["bitrate"] = bitrate
        await settings_command(update, context)
        return

    # View tracklist pagination: view:{cache_id}:{page}
    if data.startswith("view:"):
        _, cache_id, page_str = data.split(":")
        page = int(page_str)
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ Session expired. Please paste the link again.")
            return

        collection: CollectionInfo = cached["collection"]
        tracks = collection.tracks
        per_page = 10
        total_pages = (len(tracks) + per_page - 1) // per_page
        start_idx = page * per_page
        end_idx = min(start_idx + per_page, len(tracks))

        lines = [f"📋 **Tracklist: {collection.title}** (Page {page+1}/{total_pages})\n"]
        for i in range(start_idx, end_idx):
            t = tracks[i]
            dur = format_duration(t.duration_ms)
            lines.append(f"**{i+1}.** {t.title} - _{t.artist_str}_ `[{dur}]`")

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
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Download track from Spotify: dl:t:{cache_id}:{fmt}
    if data.startswith("dl:t:"):
        _, _, cache_id, chosen_fmt = data.split(":")
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ Session expired. Please send the link again.")
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

    # Download from search result: dl:s:{cache_id}:{item}
    if data.startswith("dl:s:"):
        _, _, cache_id, item = data.split(":")
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ Session expired. Please search again.")
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

    # Download collection: dl:c:{cache_id}:all
    if data.startswith("dl:c:"):
        _, _, cache_id, _ = data.split(":")
        cached = CACHE.get(cache_id)
        if not cached:
            await query.message.reply_text("⚠️ Session expired. Please paste the link again.")
            return

        collection: CollectionInfo = cached["collection"]
        user_fmt, user_bitrate = get_user_settings(context)
        total = collection.total_count

        status_msg = await query.message.reply_text(
            f"🚀 Starting batch download for **{collection.title}** ({total} songs)...",
            parse_mode=constants.ParseMode.MARKDOWN,
        )

        for idx, track in enumerate(collection.tracks, start=1):
            await status_msg.edit_text(
                f"⬇️ Downloading ({idx}/{total}): **{track.title}**...",
                parse_mode=constants.ParseMode.MARKDOWN,
            )
            await send_track_to_user(
                chat_id=query.message.chat_id,
                track=track,
                context=context,
                audio_format=user_fmt,
                bitrate=user_bitrate,
            )

        await status_msg.edit_text(
            f"✅ **Completed downloading all {total} songs for:** {collection.title}",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

# ----------------- Download & Sender Helper -----------------

async def send_track_to_user(
    chat_id: int,
    track: TrackInfo,
    context: ContextTypes.DEFAULT_TYPE,
    audio_format: str = "mp3",
    bitrate: str = "320",
):
    temp_dir = tempfile.mkdtemp(prefix="spotibot_")
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ Downloading **{track.title}** by _{track.artist_str}_\nPlease wait...",
        parse_mode=constants.ParseMode.MARKDOWN,
    )

    # Send chat action while downloading and converting
    async def keep_alive_action():
        try:
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.UPLOAD_VOICE)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    action_task = asyncio.create_task(keep_alive_action())

    try:
        # Download in worker thread
        success, filepath, status = await asyncio.to_thread(
            download_and_tag_worker,
            track,
            temp_dir,
            audio_format,
            bitrate,
        )
    finally:
        action_task.cancel()

    try:
        if not success or not os.path.exists(filepath):
            await status_msg.edit_text(f"❌ Failed to download **{track.title}**: {status}", parse_mode=constants.ParseMode.MARKDOWN)
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

        await status_msg.edit_text(f"📤 Uploading **{track.title}** to Telegram...", parse_mode=constants.ParseMode.MARKDOWN)

        # Upload audio file with extended timeouts for large files
        bot_username = context.bot.username or "UniversalSongsDownloader"
        duration_sec = int(track.duration_ms / 1000) if track.duration_ms else None

        with open(filepath, "rb") as audio_file:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=track.title,
                performer=track.artist_str,
                duration=duration_sec,
                thumbnail=thumb_bytes,
                caption=f"🎵 **{track.title}** — {track.artist_str}\n💿 {track.album}\n⚡ @{bot_username}",
                parse_mode=constants.ParseMode.MARKDOWN,
                read_timeout=300,
                write_timeout=300,
                connect_timeout=60,
            )

        # Delete status message on success
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error sending track {track.title}: {e}")
        try:
            await status_msg.edit_text(f"⚠️ Error processing {track.title}: {e}")
        except Exception:
            pass
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)

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

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Universal Songs Downloader Telegram Bot starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
