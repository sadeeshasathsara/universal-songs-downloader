import os
import logging
import requests
from typing import Optional
from mutagen.id3 import (
    ID3,
    TIT2,
    TPE1,
    TALB,
    TPE2,
    TRCK,
    TDRC,
    APIC,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from .models import TrackInfo

logger = logging.getLogger(__name__)

def _download_cover_bytes(url: str) -> Optional[tuple[bytes, str]]:
    """Downloads cover art bytes and returns (bytes, mime_type)."""
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            content_type = resp.headers.get('Content-Type', '')
            if 'png' in content_type:
                mime = 'image/png'
            else:
                mime = 'image/jpeg'
            return resp.content, mime
    except Exception as e:
        logger.warning(f"Could not download cover art from {url}: {e}")
    return None

def tag_file(file_path: str, track: TrackInfo) -> bool:
    """
    Embeds metadata and cover art into the downloaded audio file.
    Supports MP3, M4A, and FLAC.
    """
    if not os.path.exists(file_path):
        logger.error(f"Cannot tag non-existent file: {file_path}")
        return False

    ext = os.path.splitext(file_path)[1].lower()
    cover_data = _download_cover_bytes(track.cover_url) if track.cover_url else None

    try:
        if ext == '.mp3':
            _tag_mp3(file_path, track, cover_data)
        elif ext == '.m4a':
            _tag_m4a(file_path, track, cover_data)
        elif ext == '.flac':
            _tag_flac(file_path, track, cover_data)
        else:
            logger.debug(f"Tagging not supported for extension: {ext}")
            return True
        return True
    except Exception as e:
        logger.error(f"Error embedding tags into {file_path}: {e}")
        return False

def _tag_mp3(file_path: str, track: TrackInfo, cover_data: Optional[tuple[bytes, str]]):
    try:
        audio = ID3(file_path)
    except ID3NoHeaderError:
        audio = ID3()

    # Title
    audio.delall('TIT2')
    audio.add(TIT2(encoding=3, text=track.title))

    # Artist
    audio.delall('TPE1')
    audio.add(TPE1(encoding=3, text=track.artist_str))

    # Album
    if track.album:
        audio.delall('TALB')
        audio.add(TALB(encoding=3, text=track.album))

    # Album Artist
    if track.album_artist:
        audio.delall('TPE2')
        audio.add(TPE2(encoding=3, text=track.album_artist))

    # Track Number
    audio.delall('TRCK')
    audio.add(TRCK(encoding=3, text=f"{track.track_number}/{track.total_tracks}"))

    # Year / Release Date
    if track.release_date:
        audio.delall('TDRC')
        audio.add(TDRC(encoding=3, text=track.release_date))

    # Cover Art
    if cover_data:
        image_bytes, mime = cover_data
        audio.delall('APIC')
        audio.add(APIC(
            encoding=3,
            mime=mime,
            type=3,  # Front cover
            desc='Cover',
            data=image_bytes
        ))

    audio.save(file_path, v2_version=3)

def _tag_m4a(file_path: str, track: TrackInfo, cover_data: Optional[tuple[bytes, str]]):
    audio = MP4(file_path)
    audio['\xa9nam'] = [track.title]
    audio['\xa9ART'] = [track.artist_str]
    if track.album:
        audio['\xa9alb'] = [track.album]
    if track.album_artist:
        audio['aART'] = [track.album_artist]
    audio['trkn'] = [(track.track_number, track.total_tracks)]
    if track.release_date:
        audio['\xa9day'] = [track.release_date]

    if cover_data:
        image_bytes, mime = cover_data
        image_format = MP4Cover.FORMAT_PNG if mime == 'image/png' else MP4Cover.FORMAT_JPEG
        audio['covr'] = [MP4Cover(image_bytes, imageformat=image_format)]

    audio.save()

def _tag_flac(file_path: str, track: TrackInfo, cover_data: Optional[tuple[bytes, str]]):
    audio = FLAC(file_path)
    audio['title'] = track.title
    audio['artist'] = track.artist_str
    if track.album:
        audio['album'] = track.album
    if track.album_artist:
        audio['albumartist'] = track.album_artist
    audio['tracknumber'] = str(track.track_number)
    audio['totaltracks'] = str(track.total_tracks)
    if track.release_date:
        audio['date'] = track.release_date

    if cover_data:
        image_bytes, mime = cover_data
        picture = Picture()
        picture.type = 3  # Front cover
        picture.mime = mime
        picture.desc = 'Front Cover'
        picture.data = image_bytes
        audio.clear_pictures()
        audio.add_picture(picture)

    audio.save()
