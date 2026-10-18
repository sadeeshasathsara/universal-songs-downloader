import os
import sqlite3
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")

class BotDatabase:
    _instance: Optional["BotDatabase"] = None

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db_path = db_path
        self.bot_started_at = time.time()
        self._init_db()

    @classmethod
    def get_instance(cls) -> "BotDatabase":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    joined_at TEXT NOT NULL,
                    last_active TEXT NOT NULL,
                    downloads_count INTEGER DEFAULT 0,
                    is_banned INTEGER DEFAULT 0
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_stats (
                    stat_key TEXT PRIMARY KEY,
                    stat_value INTEGER DEFAULT 0
                );
            """)
            # Song cache: maps a unique song key -> Telegram file_id
            conn.execute("""
                CREATE TABLE IF NOT EXISTS song_cache (
                    cache_key TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    cached_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_cache_key ON song_cache(cache_key);
            """)
            # Initialize stats if empty
            cur = conn.execute("SELECT COUNT(*) FROM bot_stats WHERE stat_key = 'total_downloads';")
            if cur.fetchone()[0] == 0:
                conn.execute("INSERT OR IGNORE INTO bot_stats (stat_key, stat_value) VALUES ('total_downloads', 0);")
            conn.commit()

    def register_or_update_user(
        self,
        user_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = ""
    ) -> dict:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT user_id, joined_at, downloads_count, is_banned FROM users WHERE user_id = ?;", (user_id,))
            row = cursor.fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, last_name, joined_at, last_active, downloads_count, is_banned)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0);
                    """,
                    (user_id, username or "", first_name or "", last_name or "", now_iso, now_iso)
                )
                conn.commit()
                return {
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "joined_at": now_iso,
                    "last_active": now_iso,
                    "downloads_count": 0,
                    "is_banned": False,
                    "is_new": True,
                }
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, first_name = ?, last_name = ?, last_active = ?
                    WHERE user_id = ?;
                    """,
                    (username or "", first_name or "", last_name or "", now_iso, user_id)
                )
                conn.commit()
                return {
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "joined_at": row["joined_at"],
                    "last_active": now_iso,
                    "downloads_count": row["downloads_count"],
                    "is_banned": bool(row["is_banned"]),
                    "is_new": False,
                }


    def record_download(self, user_id: int):
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET downloads_count = downloads_count + 1, last_active = ?
                WHERE user_id = ?;
                """,
                (now_iso, user_id)
            )
            conn.execute(
                """
                UPDATE bot_stats
                SET stat_value = stat_value + 1
                WHERE stat_key = 'total_downloads';
                """
            )
            conn.commit()

    def is_banned(self, user_id: int) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT is_banned FROM users WHERE user_id = ?;", (user_id,))
            row = cur.fetchone()
            if row:
                return bool(row["is_banned"])
            return False

    def ban_user(self, user_id: int) -> bool:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cur = conn.execute("SELECT user_id FROM users WHERE user_id = ?;", (user_id,))
            if cur.fetchone() is None:
                conn.execute(
                    "INSERT INTO users (user_id, joined_at, last_active, is_banned) VALUES (?, ?, ?, 1);",
                    (user_id, now_iso, now_iso)
                )
            else:
                conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?;", (user_id,))
            conn.commit()
            return True

    def unban_user(self, user_id: int) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT user_id FROM users WHERE user_id = ?;", (user_id,))
            if cur.fetchone() is not None:
                conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?;", (user_id,))
                conn.commit()
                return True
            return False

    def get_broadcast_user_ids(self) -> List[int]:
        """Returns list of all active (non-banned) user IDs for sending announcements."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT user_id FROM users WHERE is_banned = 0 ORDER BY last_active DESC;")
            return [row["user_id"] for row in cur.fetchall()]

    def get_users_count(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM users;")
            return cur.fetchone()[0]

    def get_total_downloads(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT stat_value FROM bot_stats WHERE stat_key = 'total_downloads';")
            row = cur.fetchone()
            return row["stat_value"] if row else 0

    def get_banned_count(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1;")
            return cur.fetchone()[0]

    def get_user_info(self, user_id: int) -> Optional[dict]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
            return None

    def get_cached_file_id(self, cache_key: str) -> Optional[str]:
        if not cache_key:
            return None
        with self._get_connection() as conn:
            cur = conn.execute("SELECT file_id FROM song_cache WHERE cache_key = ?;", (cache_key.lower().strip(),))
            row = cur.fetchone()
            if row:
                return row["file_id"]
            return None

    def set_cached_file_id(self, cache_key: str, file_id: str, title: str = "", artist: str = ""):
        if not cache_key or not file_id:
            return
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO song_cache (cache_key, file_id, title, artist, cached_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (cache_key.lower().strip(), file_id, title or "", artist or "", now_iso)
            )
            conn.commit()

    def get_cached_songs_count(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM song_cache;")
            return cur.fetchone()[0]

