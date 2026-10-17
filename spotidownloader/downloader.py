import os
import re
import logging
from typing import Optional, Callable
import static_ffmpeg
static_ffmpeg.add_paths()

import yt_dlp
from .models import TrackInfo
from .tagger import tag_file

logger = logging.getLogger(__name__)

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
            # Prefer audio-only, fall all the way back to any format — handles VPS-restricted
            # videos where only format 18 (combined video+audio mp4) is available
            'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio/best[acodec!=none]/best',
            'format_sort': ['abr', 'tbr'],
            'outtmpl': f"{temp_basename}.%(ext)s",
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': self.audio_format,
                'preferredquality': self.bitrate,
            }],
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'retries': 5,
            'fragment_retries': 5,
            'js_runtimes': {'node': {}},
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'mweb', 'web'],
                }
            },
            'progress_hooks': [ydl_hook],
            # Allow remote EJS solver script — needed on datacenter IPs (VPS) to bypass n-challenge
            'remote_components': 'ejs:github',
        }

        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file
            logger.info(f"Using YouTube cookie file: {cookie_file}")

        if os.path.exists(ytdlp_conf):
            ydl_opts['config_location'] = ytdlp_conf
            logger.info(f"Using yt-dlp config: {ytdlp_conf}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(query, download=False)
                entries = search_results.get('entries', []) if search_results else []
                if not entries:
                    fallback_query = f"ytsearch5:{track.query}"
                    search_results = ydl.extract_info(fallback_query, download=False)
                    entries = search_results.get('entries', []) if search_results else []

                if not entries:
                    return False, target_path, f"No audio match found on YouTube for '{track.query}'"

                candidates = self._rank_candidates(entries, track)
                download_succeeded = False
                last_error = ""

                # Try candidates in order; if one fails (e.g. 403 Forbidden), failover to the next!
                for candidate in candidates:
                    video_url = candidate.get('webpage_url') or candidate.get('url') or f"https://www.youtube.com/watch?v={candidate.get('id')}"
                    try:
                        ydl.download([video_url])
                        if os.path.exists(expected_tmp_out) and os.path.getsize(expected_tmp_out) > 1024:
                            download_succeeded = True
                            break
                    except Exception as err:
                        last_error = str(err)
                        logger.warning(f"Candidate {video_url} failed with: {err}, trying next candidate...")
                        if os.path.exists(expected_tmp_out):
                            try:
                                os.remove(expected_tmp_out)
                            except Exception:
                                pass
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
