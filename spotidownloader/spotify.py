import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List
from .models import TrackInfo, CollectionInfo

logger = logging.getLogger(__name__)

SPOTIFY_URL_PATTERN = re.compile(
    r'(?:spotify:(track|album|playlist|artist):|(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/))([a-zA-Z0-9]+)'
)

BOT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

def parse_spotify_url(url: str) -> Optional[Tuple[str, str]]:
    """
    Parses a Spotify URL or URI and returns (item_type, item_id).
    Example: 'https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT' -> ('track', '4cOdK2wGLETKBW3PvgPWqT')
    """
    match = SPOTIFY_URL_PATTERN.search(url.strip())
    if not match:
        return None
    item_type = match.group(1) or match.group(2)
    item_id = match.group(3)
    return item_type, item_id

def _get_og_metadata(url: str) -> dict:
    """Scrapes OpenGraph tags by simulating a bot crawler."""
    meta = {}
    try:
        resp = requests.get(url, headers=BOT_HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup.find_all('meta'):
                prop = tag.get('property') or tag.get('name')
                val = tag.get('content')
                if prop and val:
                    meta[prop] = val
    except Exception as e:
        logger.debug(f"Failed to fetch OpenGraph metadata for {url}: {e}")
    return meta

def _get_embed_entity(item_type: str, item_id: str) -> Optional[dict]:
    """Fetches and parses the __NEXT_DATA__ entity JSON from Spotify's embed view."""
    embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
    try:
        resp = requests.get(embed_url, headers=BROWSER_HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            if script and script.string:
                data = json.loads(script.string)
                return data.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity')
    except Exception as e:
        logger.debug(f"Failed to fetch embed entity for {item_type}/{item_id}: {e}")
    return None

class SpotifyClient:
    def __init__(self):
        self.session = requests.Session()

    def fetch(self, url: str) -> CollectionInfo:
        parsed = parse_spotify_url(url)
        if not parsed:
            raise ValueError(f"Invalid or unsupported Spotify URL: '{url}'. Please provide a valid track, album, or playlist URL.")
        
        item_type, item_id = parsed
        
        if item_type == 'track':
            return self._fetch_track(item_id)
        elif item_type == 'album':
            return self._fetch_album(item_id)
        elif item_type == 'playlist':
            return self._fetch_playlist(item_id)
        elif item_type == 'artist':
            return self._fetch_artist(item_id)
        else:
            raise ValueError(f"Unsupported Spotify type: {item_type}")

    def _fetch_track(self, track_id: str) -> CollectionInfo:
        track_url = f"https://open.spotify.com/track/{track_id}"
        og_meta = _get_og_metadata(track_url)
        entity = _get_embed_entity('track', track_id)

        title = ""
        artists = []
        album = "Unknown Album"
        album_artist = ""
        cover_url = og_meta.get('og:image', '')
        duration_ms = int(og_meta.get('music:duration', 0)) * 1000
        release_date = og_meta.get('music:release_date', '')

        if entity:
            title = entity.get('title') or entity.get('name') or title
            if 'artists' in entity and entity['artists']:
                artists = [a.get('name') for a in entity['artists'] if a.get('name')]
            if entity.get('duration'):
                duration_ms = entity['duration']
            if entity.get('releaseDate', {}).get('isoString'):
                release_date = entity['releaseDate']['isoString'][:10]

        if not title:
            title = og_meta.get('og:title', 'Unknown Track')
        
        if not artists:
            musician = og_meta.get('music:musician_description')
            if musician:
                artists = [musician]
            elif 'og:description' in og_meta:
                # Often formatted like "Artist · Song · Year"
                parts = og_meta['og:description'].split('·')
                if parts:
                    artists = [parts[0].strip()]

        # Try to parse album name from og:description
        desc = og_meta.get('og:description', '')
        if '·' in desc:
            parts = [p.strip() for p in desc.split('·')]
            if len(parts) >= 2 and parts[1] != 'Song':
                album = parts[1]
        
        if not album_artist and artists:
            album_artist = artists[0]

        track = TrackInfo(
            title=title,
            artists=artists if artists else ["Unknown Artist"],
            album=album,
            album_artist=album_artist,
            cover_url=cover_url,
            duration_ms=duration_ms,
            release_date=release_date,
            track_number=1,
            total_tracks=1,
            spotify_id=track_id,
            spotify_url=track_url,
        )

        return CollectionInfo(
            type="track",
            title=f"{track.artist_str} - {track.title}",
            description=f"Track by {track.artist_str}",
            cover_url=cover_url,
            tracks=[track],
            spotify_id=track_id,
            spotify_url=track_url,
        )

    def _fetch_album(self, album_id: str) -> CollectionInfo:
        album_url = f"https://open.spotify.com/album/{album_id}"
        og_meta = _get_og_metadata(album_url)
        entity = _get_embed_entity('album', album_id)

        if not entity:
            raise RuntimeError(f"Could not load album metadata from Spotify for album ID: {album_id}")

        album_title = entity.get('name') or entity.get('title') or og_meta.get('og:title', 'Unknown Album')
        album_artist = entity.get('subtitle', '') or og_meta.get('music:musician_description', 'Various Artists')
        cover_url = og_meta.get('og:image', '')
        release_date = ""
        if entity.get('releaseDate', {}).get('isoString'):
            release_date = entity['releaseDate']['isoString'][:10]
        elif og_meta.get('music:release_date'):
            release_date = og_meta['music:release_date']

        raw_tracks = entity.get('trackList', [])
        total_tracks = len(raw_tracks)
        tracks: List[TrackInfo] = []

        for idx, t in enumerate(raw_tracks, start=1):
            t_title = t.get('title', 'Unknown Title')
            t_artists = [a.strip() for a in t.get('subtitle', album_artist).split(',') if a.strip()]
            t_uri = t.get('uri', '')
            t_id = t_uri.split(':')[-1] if ':' in t_uri else ''
            t_duration = t.get('duration', 0)

            tracks.append(TrackInfo(
                title=t_title,
                artists=t_artists if t_artists else [album_artist],
                album=album_title,
                album_artist=album_artist,
                cover_url=cover_url,
                duration_ms=t_duration,
                release_date=release_date,
                track_number=idx,
                total_tracks=total_tracks,
                spotify_id=t_id,
                spotify_url=f"https://open.spotify.com/track/{t_id}" if t_id else album_url,
            ))

        return CollectionInfo(
            type="album",
            title=album_title,
            description=f"Album by {album_artist} ({total_tracks} tracks)",
            cover_url=cover_url,
            tracks=tracks,
            spotify_id=album_id,
            spotify_url=album_url,
        )

    def _fetch_playlist(self, playlist_id: str) -> CollectionInfo:
        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        og_meta = _get_og_metadata(playlist_url)
        entity = _get_embed_entity('playlist', playlist_id)

        if not entity:
            raise RuntimeError(f"Could not load playlist metadata from Spotify for playlist ID: {playlist_id}")

        playlist_title = entity.get('name') or entity.get('title') or og_meta.get('og:title', 'Spotify Playlist')
        # Cover art from og or entity
        cover_url = og_meta.get('og:image', '')
        if not cover_url:
            sources = entity.get('coverArt', {}).get('sources', [])
            if sources:
                cover_url = sources[0].get('url', '')

        raw_tracks = entity.get('trackList', [])
        total_tracks = len(raw_tracks)
        tracks: List[TrackInfo] = []

        for idx, t in enumerate(raw_tracks, start=1):
            t_title = t.get('title', 'Unknown Title')
            subtitle = t.get('subtitle', '')
            t_artists = [a.strip() for a in subtitle.split(',') if a.strip()]
            t_uri = t.get('uri', '')
            t_id = t_uri.split(':')[-1] if ':' in t_uri else ''
            t_duration = t.get('duration', 0)

            tracks.append(TrackInfo(
                title=t_title,
                artists=t_artists if t_artists else ["Unknown Artist"],
                album=playlist_title,
                album_artist=t_artists[0] if t_artists else "Various Artists",
                cover_url=cover_url,
                duration_ms=t_duration,
                release_date="",
                track_number=idx,
                total_tracks=total_tracks,
                spotify_id=t_id,
                spotify_url=f"https://open.spotify.com/track/{t_id}" if t_id else playlist_url,
            ))

        return CollectionInfo(
            type="playlist",
            title=playlist_title,
            description=f"Playlist ({total_tracks} tracks)",
            cover_url=cover_url,
            tracks=tracks,
            spotify_id=playlist_id,
            spotify_url=playlist_url,
        )

    def _fetch_artist(self, artist_id: str) -> CollectionInfo:
        artist_url = f"https://open.spotify.com/artist/{artist_id}"
        og_meta = _get_og_metadata(artist_url)
        entity = _get_embed_entity('artist', artist_id)

        artist_name = og_meta.get('og:title')
        if not artist_name and entity:
            artist_name = entity.get('name') or entity.get('title')
        if not artist_name:
            artist_name = "Unknown Artist"

        cover_url = og_meta.get('og:image', '')
        raw_tracks = []
        if entity:
            raw_tracks = entity.get('trackList', [])

        total_tracks = len(raw_tracks)
        tracks: List[TrackInfo] = []

        for idx, t in enumerate(raw_tracks, start=1):
            t_title = t.get('title', 'Unknown Title')
            subtitle = t.get('subtitle', artist_name)
            t_artists = [a.strip() for a in subtitle.split(',') if a.strip()]
            t_uri = t.get('uri', '')
            t_id = t_uri.split(':')[-1] if ':' in t_uri else ''
            t_duration = t.get('duration', 0)

            tracks.append(TrackInfo(
                title=t_title,
                artists=t_artists if t_artists else [artist_name],
                album=f"{artist_name} - Top Tracks",
                album_artist=artist_name,
                cover_url=cover_url,
                duration_ms=t_duration,
                release_date="",
                track_number=idx,
                total_tracks=total_tracks,
                spotify_id=t_id,
                spotify_url=f"https://open.spotify.com/track/{t_id}" if t_id else artist_url,
            ))

        return CollectionInfo(
            type="artist",
            title=f"{artist_name} - Top Tracks",
            description=f"Top tracks by {artist_name}",
            cover_url=cover_url,
            tracks=tracks,
            spotify_id=artist_id,
            spotify_url=artist_url,
        )
