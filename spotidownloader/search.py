import logging
import requests
from typing import List, Optional
import yt_dlp
from .models import TrackInfo

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

def search_itunes(query: str, limit: int = 5) -> List[TrackInfo]:
    """
    Searches the official iTunes Music API for studio track metadata and high-res artwork.
    No API keys required.
    """
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": limit,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    
    tracks: List[TrackInfo] = []
    try:
        resp = requests.get(ITUNES_SEARCH_URL, params=params, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            for r in results:
                title = r.get("trackName", "Unknown Title")
                artist = r.get("artistName", "Unknown Artist")
                album = r.get("collectionName", "Unknown Album")
                duration_ms = r.get("trackTimeMillis", 0)
                release_date = r.get("releaseDate", "")[:10]
                track_num = r.get("trackNumber", 1)
                total_tracks = r.get("trackCount", 1)
                
                # Fetch 1000x1000 high-resolution artwork
                raw_art = r.get("artworkUrl100", "")
                cover_url = raw_art.replace("100x100bb.jpg", "1000x1000bb.jpg") if raw_art else ""
                
                track_id = str(r.get("trackId", ""))
                genre = r.get("primaryGenreName", "")

                tracks.append(TrackInfo(
                    title=title,
                    artists=[artist],
                    album=album,
                    album_artist=artist,
                    genre=genre,
                    cover_url=cover_url,
                    duration_ms=duration_ms,
                    release_date=release_date,
                    track_number=track_num,
                    total_tracks=total_tracks,
                    spotify_id=track_id,
                    spotify_url=spotify_url,
                ))
    except Exception as e:
        logger.warning(f"iTunes search failed: {e}")

    return tracks

def search_youtube_fallback(query: str, limit: int = 5) -> List[TrackInfo]:
    """
    Fallback search using yt-dlp to query YouTube / YouTube Music.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": f"ytsearch{limit}",
    }
    tracks: List[TrackInfo] = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = res.get("entries", []) if res else []
            for e in entries:
                if not e:
                    continue
                title = e.get("title", "Unknown Title")
                uploader = e.get("uploader", "Unknown Artist")
                duration = int(e.get("duration", 0) or 0) * 1000
                url = e.get("url") or e.get("webpage_url") or ""
                thumbnail = e.get("thumbnail") or ""

                # Clean up title if format is "Artist - Title"
                artists = [uploader]
                clean_title = title
                if " - " in title:
                    parts = title.split(" - ", 1)
                    artists = [parts[0].strip()]
                    clean_title = parts[1].strip()

                tracks.append(TrackInfo(
                    title=clean_title,
                    artists=artists,
                    album="Single",
                    album_artist=artists[0],
                    cover_url=thumbnail,
                    duration_ms=duration,
                    release_date="",
                    track_number=1,
                    total_tracks=1,
                    spotify_id=e.get("id", ""),
                    spotify_url=url,
                ))
    except Exception as e:
        logger.warning(f"YouTube search fallback failed: {e}")

    return tracks

def search_songs(query: str, limit: int = 5) -> List[TrackInfo]:
    """
    Searches for songs using iTunes catalog first, falling back to YouTube if necessary.
    """
    results = search_itunes(query, limit=limit)
    if not results:
        results = search_youtube_fallback(query, limit=limit)
    return results
