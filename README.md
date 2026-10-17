# 🎵 SpotiDownloader CLI

A high-performance, command-line Spotify downloader built with Python, `yt-dlp`, and `mutagen`. It fetches metadata from Spotify, downloads high-fidelity audio from YouTube/YouTube Music, converts it to your chosen format (MP3 up to 320kbps, M4A, or FLAC), and automatically embeds complete ID3 tags and high-resolution album artwork.

---

## ✨ Features

- **No Spotify API Keys Required**: Seamlessly extracts track, album, and playlist metadata out-of-the-box.
- **Supports All Spotify URLs**:
  - 🎵 **Individual Tracks**: `https://open.spotify.com/track/...`
  - 💿 **Full Albums**: `https://open.spotify.com/album/...` (batch download and organization)
  - 📜 **Playlists**: `https://open.spotify.com/playlist/...`
  - 🎤 **Artist Top Tracks**: `https://open.spotify.com/artist/...`
- **Embedded Metadata & High-Res Artwork**:
  - Song Title
  - Contributing Artists
  - Album Title & Album Artist
  - Track Number & Total Count
  - Release Date / Year
  - High-resolution cover artwork directly embedded into audio files
- **Audio Quality & Formats**:
  - MP3 (320kbps, 256kbps, 192kbps)
  - M4A (AAC)
  - FLAC (Lossless)
- **Built-in FFmpeg**: Automatically manages FFmpeg via `static-ffmpeg` (no manual system PATH configuration needed).
- **Rich Terminal UI**:
  - Interactive mode with prompt loop
  - Multi-bar progress tracking (overall playlist progress + per-track download stream progress)
  - Summary report table with file sizes and completion statuses
  - Smart deduplication (skips already downloaded tracks)

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.9+
- Internet connection

### 2. Setup (If not already installed)
Create and activate your virtual environment:

```powershell
# Create virtual environment
python -m venv .venv

# Activate on Windows
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

---

## 🎧 Usage

### Interactive Mode
Simply run the script without any arguments:

```powershell
python main.py
```
*(Or on Windows, just double-click or run `.\run.bat`)*

You will be presented with a prompt:
```
Enter Spotify Track, Album, or Playlist URL (or 'q' to quit):
```

### Direct CLI Commands

#### 1. Download a Single Song
```powershell
python main.py https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT
```

#### 2. Download an Entire Album
```powershell
python main.py https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy
```
*Albums are automatically organized into subfolders with the album name.*

#### 3. Download a Full Playlist
```powershell
python main.py https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
```

#### 4. Custom Output Directory
```powershell
python main.py https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT -o "C:\Music\Favorites"
```

#### 5. Custom Format and Quality
```powershell
# FLAC Lossless
python main.py https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT -f flac

# M4A format
python main.py https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT -f m4a

# 256kbps MP3
python main.py https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT -b 256
```

---

## ⚙️ CLI Options & Flags

| Flag | Default | Description |
|---|---|---|
| `url` | *None* | Spotify track, album, or playlist URL (runs interactive mode if omitted) |
| `-o`, `--output` | `downloads` | Destination folder for downloaded songs |
| `-f`, `--format` | `mp3` | Audio container format (`mp3`, `m4a`, `flac`) |
| `-b`, `--bitrate` | `320` | Audio bitrate in kbps (`320`, `256`, `192`, `128`) |
| `--no-skip` | `False` | Force re-download even if the file already exists |
| `--no-subfolder` | `False` | Do not create separate subfolders for albums/playlists |
| `-h`, `--help` | - | Display help menu with all options |

---

## 📁 Project Structure

```
├── spotidownloader/
│   ├── __init__.py         # Package entry
│   ├── models.py           # TrackInfo & CollectionInfo data models
│   ├── spotify.py          # Spotify metadata scraping & URL resolver
│   ├── downloader.py       # Audio search, download & FFmpeg conversion pipeline
│   ├── tagger.py           # Mutagen ID3/M4A/FLAC tag & album art embedder
│   └── cli.py              # Rich terminal interface & progress display
├── main.py                 # Main CLI executable
├── run.bat                 # Convenient Windows 1-click launcher
├── requirements.txt        # Package dependencies
└── README.md               # Documentation
```
