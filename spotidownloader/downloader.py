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

            if score > best_score:
                best_score = score
                best_entry = entry

        return best_entry or entries[0]

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
        query = f"ytsearch4:{track.query} official audio"

        # Build yt-dlp options
        temp_basename = os.path.join(
            os.path.dirname(target_path),
            f".tmp_{track.spotify_id or abs(hash(track.title))}"
        )

        def ydl_hook(d):
            if progress_callback:
                progress_callback(d)

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f"{temp_basename}.%(ext)s",
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': self.audio_format,
                'preferredquality': self.bitrate,
            }],
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [ydl_hook],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # First extract search entries without immediately downloading all
                search_results = ydl.extract_info(query, download=False)
                entries = search_results.get('entries', []) if search_results else []
                if not entries:
                    # Fallback search with broader terms
                    fallback_query = f"ytsearch4:{track.query}"
                    search_results = ydl.extract_info(fallback_query, download=False)
                    entries = search_results.get('entries', []) if search_results else []

                if not entries:
                    return False, target_path, f"No audio match found on YouTube for '{track.query}'"

                best = self._select_best_candidate(entries, track)
                if not best:
                    return False, target_path, f"Failed to select match for '{track.query}'"

                # Download the selected candidate
                video_url = best.get('webpage_url') or best.get('url') or f"https://www.youtube.com/watch?v={best.get('id')}"
                ydl.download([video_url])

            # The postprocessed output file
            expected_tmp_out = f"{temp_basename}.{self.audio_format}"
            if os.path.exists(expected_tmp_out):
                # Move to final target destination
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.replace(expected_tmp_out, target_path)

                # Embed metadata and album artwork
                tag_file(target_path, track)
                return True, target_path, "downloaded"
            else:
                return False, target_path, "Output file was not generated"

        except Exception as e:
            logger.error(f"Error downloading {track.query}: {e}")
            # Clean up temp file if present
            temp_file = f"{temp_basename}.{self.audio_format}"
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            return False, target_path, str(e)
