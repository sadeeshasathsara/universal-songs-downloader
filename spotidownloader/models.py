from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class TrackInfo:
    title: str
    artists: List[str]
    album: str = "Unknown Album"
    album_artist: str = ""
    genre: str = ""
    cover_url: str = ""
    duration_ms: int = 0
    release_date: str = ""
    track_number: int = 1
    total_tracks: int = 1
    spotify_id: str = ""
    spotify_url: str = ""
    album_id: str = ""  # iTunes collectionId, used for album track lookups

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists) if self.artists else "Unknown Artist"

    @property
    def query(self) -> str:
        primary_artist = self.artists[0] if self.artists else ""
        return f"{primary_artist} - {self.title}"

    @property
    def clean_filename(self) -> str:
        # Avoid illegal characters on Windows and other OSes: \ / : * ? " < > |
        safe_title = "".join(c for c in self.title if c not in r'\/:*?"<>|').strip()
        safe_artist = "".join(c for c in self.artist_str if c not in r'\/:*?"<>|').strip()
        return f"{safe_artist} - {safe_title}"

@dataclass
class CollectionInfo:
    type: str  # "track", "album", "playlist", "artist"
    title: str
    description: str = ""
    cover_url: str = ""
    tracks: List[TrackInfo] = field(default_factory=list)
    spotify_id: str = ""
    spotify_url: str = ""

    @property
    def total_count(self) -> int:
        return len(self.tracks)
