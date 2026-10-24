import os
import re
import time
import logging
from typing import Optional, Callable
import static_ffmpeg
static_ffmpeg.add_paths()

import yt_dlp
from .models import TrackInfo
from .tagger import tag_file

logger = logging.getLogger(__name__)

# In-memory cache: {video_id: {'url': str, 'expires': float}}
# Avoids re-running the 27–54s Deno JS challenge for the same video
_stream_url_cache: dict = {}
_STREAM_URL_TTL = 4 * 3600  # YouTube direct URLs are valid ~6h; cache for 4h to be safe

def get_cookie_file() -> Optional[str]:
    """Finds cookies.txt file path if available to bypass datacenter IP restrictions."""
    env_path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_cookie = os.path.join(base_dir, "data", "cookies.txt")
    if os.path.exists(data_cookie):
        return data_cookie

    root_cookie = os.path.join(base_dir, "cookies.txt")
    if os.path.exists(root_cookie):
        return root_cookie

    return None

class AudioDownloader:
    def __init__(
        self,
        output_dir: str = "downloads",
        audio_format: str = "mp3",
        bitrate: str = "320",
        skip_existing: bool = True,
    ):
        self.output_dir = os.path.abspath(output_dir)
        self.audio_format = audio_format.lower()
        self.bitrate = bitrate.replace("k", "").strip()
        self.skip_existing = skip_existing
        os.makedirs(self.output_dir, exist_ok=True)

    def get_target_filepath(self, track: TrackInfo, subfolder: Optional[str] = None) -> str:
        folder = os.path.join(self.output_dir, subfolder) if subfolder else self.output_dir
        os.makedirs(folder, exist_ok=True)
        filename = f"{track.clean_filename}.{self.audio_format}"
        return os.path.join(folder, filename)

    def _select_best_candidate(self, entries: list, track: TrackInfo) -> Optional[dict]:
        """
        Scores YouTube search candidates based on duration match, title keywords,
        and channel metadata.
        """
        if not entries:
            return None

        best_entry = None
        best_score = -9999.0
        target_duration = (track.duration_ms / 1000.0) if track.duration_ms else 0

        for entry in entries:
            if not entry:
                continue
            score = 0.0
            yt_title = (entry.get('title') or '').lower()
            uploader = (entry.get('uploader') or '').lower()
            duration = entry.get('duration') or 0

            # Duration comparison
            if target_duration > 0 and duration > 0:
                diff = abs(duration - target_duration)
                if diff <= 5:
                    score += 50.0
                elif diff <= 12:
                    score += 30.0
                elif diff <= 25:
                    score += 10.0
                elif diff > 60:
                    score -= 40.0  # Likely extended, 1-hour, or music video dialogue intro

            # Title keyword matching
            track_title_lower = track.title.lower()
            if track_title_lower in yt_title:
                score += 25.0

            # Boost official audio / topic / lyrics
            if 'official audio' in yt_title or 'official music video' in yt_title:
                score += 15.0
            if 'topic' in uploader or 'vevo' in uploader:
                score += 10.0
            if 'lyrics' in yt_title:
                score += 5.0

            # Penalize unwanted variants unless the track itself is that variant
            for penalty_word in ['karaoke', 'instrumental', 'remix', 'cover', 'live', 'slowed', 'reverb', 'bass boosted']:
                if penalty_word in yt_title and penalty_word not in track_title_lower:
                    score -= 50.0

    def _rank_candidates(self, entries: list, track: TrackInfo) -> list:
        """
        Ranks YouTube search candidates from highest to lowest score.
        """
        scored = []
        target_duration = (track.duration_ms / 1000.0) if track.duration_ms else 0
        track_title_lower = track.title.lower()

        for entry in entries:
            if not entry:
                continue
            score = 0.0
            yt_title = (entry.get('title') or '').lower()
            uploader = (entry.get('uploader') or '').lower()
            duration = entry.get('duration') or 0

            # Duration comparison
            if target_duration > 0 and duration > 0:
                diff = abs(duration - target_duration)
                if diff <= 5:
                    score += 50.0
                elif diff <= 12:
                    score += 30.0
                elif diff <= 25:
                    score += 10.0
                elif diff > 60:
                    score -= 40.0

            # Title matching
            if track_title_lower in yt_title:
                score += 25.0

            if 'official audio' in yt_title or 'official music video' in yt_title:
                score += 15.0
            if 'topic' in uploader or 'vevo' in uploader:
                score += 10.0
            if 'lyrics' in yt_title:
                score += 5.0

            for penalty_word in ['karaoke', 'instrumental', 'remix', 'cover', 'live', 'slowed', 'reverb']:
                if penalty_word in yt_title and penalty_word not in track_title_lower:
                    score -= 40.0

            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored]

    def download_track(
        self,
        track: TrackInfo,
        subfolder: Optional[str] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ) -> tuple[bool, str, str]:
        """
        Downloads a single track, converts it to the requested audio format,
        and applies ID3 metadata and cover art.

        Returns: (success: bool, filepath: str, status_message: str)
        """
        target_path = self.get_target_filepath(track, subfolder)

        if self.skip_existing and os.path.exists(target_path) and os.path.getsize(target_path) > 1024:
            return True, target_path, "already_exists"

        # Search query formulation
        query = f"ytsearch5:{track.query} audio"

        temp_basename = os.path.join(
            os.path.dirname(target_path),
            f".tmp_{track.spotify_id or abs(hash(track.title))}"
        )
        expected_tmp_out = f"{temp_basename}.{self.audio_format}"

        def ydl_hook(d):
            if progress_callback:
                progress_callback(d)

        cookie_file = get_cookie_file()

        # Check if a yt-dlp config file exists (used for --remote-components etc on VPS)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ytdlp_conf = os.path.join(base_dir, "data", "yt-dlp.conf")

        ydl_opts = {
            # Prefer audio-only formats; fall back to best combined
            'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': f"{temp_basename}.%(ext)s",
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': self.audio_format,
                'preferredquality': self.bitrate,
            }],
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'retries': 10,
            'fragment_retries': 10,
            'http_chunk_size': 10485760,  # 10MB chunks — avoids partial 403s on large streams
            'progress_hooks': [ydl_hook],
            # Force web player client — datacenter IPs get 403 with TV/Android clients
            # but the web client (with cookies) is more permissive
            'extractor_args': {
                'youtube': {
                    'player_client': ['web'],
                }
            },
            # Allow remote EJS solver script as a list of components
            'remote_components': ['ejs:github'],
        }

        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file
            logger.info(f"Using YouTube cookie file: {cookie_file}")

        if os.path.exists(ytdlp_conf):
            ydl_opts['config_location'] = ytdlp_conf
            logger.info(f"Using yt-dlp config: {ytdlp_conf}")

        try:
            # Step 1: Fast flat search (completes in < 1s instead of loading all video pages)
            search_opts = dict(ydl_opts)
            search_opts['extract_flat'] = 'in_playlist'

            entries = []
            with yt_dlp.YoutubeDL(search_opts) as ydl_search:
                search_results = ydl_search.extract_info(query, download=False)
                entries = search_results.get('entries', []) if search_results else []
                if not entries:
                    fallback_query = f"ytsearch5:{track.query}"
                    search_results = ydl_search.extract_info(fallback_query, download=False)
                    entries = search_results.get('entries', []) if search_results else []

            if not entries:
                return False, target_path, f"No audio match found on YouTube for '{track.query}'"

            candidates = self._rank_candidates(entries, track)
            download_succeeded = False
            last_error = ""

            # Step 2: Download candidates in ranked order; failover if unavailable or blocked.
            # We use a two-step approach per candidate:
            #   a) Check the in-memory stream URL cache (avoids 27-54s Deno JS challenge on repeat)
            #   b) If not cached: extract_info(download=False) to resolve + cache the direct URL
            #   c) Download using the direct stream URL (Deno already ran in step b, not again)
            extract_opts = dict(ydl_opts)
            extract_opts.pop('outtmpl', None)
            extract_opts.pop('postprocessors', None)
            extract_opts.pop('progress_hooks', None)

            for candidate in candidates:
                video_id = candidate.get('id')
                video_url = candidate.get('webpage_url') or candidate.get('url') or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
                if not video_url:
                    continue

                try:
                    # --- Cache lookup ---
                    cached = _stream_url_cache.get(video_id) if video_id else None
                    if cached and cached['expires'] > time.time():
                        direct_url = cached['url']
                        logger.info(f"Cache HIT for {video_id} — skipping Deno challenge")
                    else:
                        # Run full extraction (Deno JS challenge happens here, once)
                        logger.info(f"Cache MISS for {video_id} — running yt-dlp extraction (Deno challenge)")
                        with yt_dlp.YoutubeDL(extract_opts) as ydl_extract:
                            info = ydl_extract.extract_info(video_url, download=False)

                        if not info:
                            last_error = f"Could not extract info for {video_url}"
                            logger.warning(last_error)
                            continue

                        # Get the best audio URL from the resolved formats
                        formats = info.get('formats') or []
                        # Prefer audio-only formats; fall back to url on info dict
                        audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('url')]
                        if audio_formats:
                            # Pick highest quality audio
                            audio_formats.sort(key=lambda f: f.get('abr') or f.get('tbr') or 0, reverse=True)
                            direct_url = audio_formats[0]['url']
                        elif info.get('url'):
                            direct_url = info['url']
                        else:
                            last_error = f"No stream URL found in extracted info for {video_url}"
                            logger.warning(last_error)
                            continue

                        # Store in cache
                        if video_id:
                            _stream_url_cache[video_id] = {
                                'url': direct_url,
                                'expires': time.time() + _STREAM_URL_TTL,
                            }
                            logger.info(f"Cached stream URL for {video_id} (TTL {_STREAM_URL_TTL}s)")

                    # --- Download using the YouTube watch URL ---
                    # We MUST pass the YouTube page URL (not the raw CDN stream URL).
                    # Passing a raw googlevideo.com URL makes yt-dlp skip format resolution,
                    # which causes ffprobe to fail reading the audio codec ("unable to obtain
                    # file audio codec with ffprobe"). The cache above just speeds up
                    # extract_info; the actual download call always uses the canonical video URL.
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])

                    if os.path.exists(expected_tmp_out) and os.path.getsize(expected_tmp_out) > 1024:
                        download_succeeded = True
                        break
                    else:
                        last_error = f"Video {video_url} did not yield an audio stream"
                        logger.warning(f"Candidate {video_url} produced no audio file, failing over...")
                        # Evict bad cache entry
                        if video_id and video_id in _stream_url_cache:
                            del _stream_url_cache[video_id]

                except Exception as err:
                    last_error = str(err)
                    logger.warning(f"Candidate {video_url} failed ({err}), failing over to next candidate...")
                    if os.path.exists(expected_tmp_out):
                        try:
                            os.remove(expected_tmp_out)
                        except Exception:
                            pass
                    # Evict potentially stale cache entry on error
                    if video_id and video_id in _stream_url_cache:
                        del _stream_url_cache[video_id]
                    continue

            if not download_succeeded:
                return False, target_path, f"Download failed across all candidates: {last_error}"

            # Finalize output file
            if os.path.exists(expected_tmp_out):
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.replace(expected_tmp_out, target_path)

                tag_file(target_path, track)
                return True, target_path, "downloaded"
            else:
                return False, target_path, "Output file was not generated"

        except Exception as e:
            logger.error(f"Error downloading {track.query}: {e}")
            if os.path.exists(expected_tmp_out):
                try:
                    os.remove(expected_tmp_out)
                except Exception:
                    pass
            return False, target_path, str(e)
