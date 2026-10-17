# 🎵 SpotiDownloader CLI

A high-performance, command-line Spotify downloader and song search tool built with Python, `yt-dlp`, and `mutagen`.

Download tracks, albums, playlists, or simply search by song name! It fetches studio metadata, downloads high-fidelity audio from YouTube/YouTube Music, converts it to your chosen format (MP3 up to 320kbps, M4A, or FLAC), and automatically embeds complete ID3 tags and high-resolution album artwork (up to 1000x1000).

---

## ✨ Features

- **🤖 Universal Telegram Bot Integration**:
  - Full-featured Telegram bot (`@universalsongsdownloaderbot`)
  - Rich inline buttons, thumbnail previews, format pickers, and live progress
  - Audio files directly uploaded into Telegram chat with embedded metadata and cover art!
- **🔍 Direct Song Search**:
  - Don't have a Spotify URL? Just type any song name or artist (e.g. `Blinding Lights` or `Ed Sheeran Shape of You`).
  - Displays top search results with artist, album, and duration.
  - Interactive selection (pick 1, multi-select like `1,3`, or `all`).
  - `--yes` (`-y`) flag for instant auto-download of top match.
- **🎵 All Spotify URLs Supported**:
  - 🎶 **Individual Tracks**: `https://open.spotify.com/track/...`
  - 💿 **Full Albums**: `https://open.spotify.com/album/...` (batch download and organization)
  - 📜 **Playlists**: `https://open.spotify.com/playlist/...`
  - 🎤 **Artist Top Tracks**: `https://open.spotify.com/artist/...`
- **No Spotify API Keys or Account Required**: Seamlessly works out-of-the-box.
- **Embedded Metadata & High-Res Artwork**:
  - Song Title & Artists
  - Album Title & Album Artist
  - Track Number & Total Count
  - Release Date / Year
  - High-resolution cover artwork (up to 1000x1000) directly embedded into audio files
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
```powershell
# Create virtual environment
python -m venv .venv

# Activate on Windows
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

---

## 🤖 Running the Telegram Bot

Your bot token is configured in `.env`.

To launch the bot:
```powershell
# Windows 1-click batch runner
.\run_bot.bat

# Or run directly via Python
.\.venv\Scripts\python.exe run_bot.py
```

### Telegram Bot Capabilities:
1. **Send `/start`**: Welcome panel with navigation buttons.
2. **Type Any Song Name**: Displays top 5 matching songs with cover thumbnail and instant `[ ⬇️ #1 ]` to `[ ⬇️ #5 ]` buttons or `[ ⬇️ Download All ]`.
3. **Paste Spotify Link**: Automatically detects Tracks, Albums, and Playlists.
   - For Tracks: Shows album cover, song length, artist, and format download buttons.
   - For Albums/Playlists: Shows cover art, song count, `[ ⬇️ Download All ]`, and `[ 📋 View Tracklist ]` pagination.
4. **Settings (`/settings`)**: Choose preferred audio quality: 320kbps MP3, 192kbps MP3, FLAC lossless, or M4A.
5. **Direct Delivery**: Audio is uploaded directly to your Telegram chat with full ID3 metadata and album art.

---

## 🎧 CLI Usage

### 1. Interactive Mode
Simply run the tool without arguments:

```powershell
.\run.bat
# or
python main.py
```

You can type **any song name** or paste **any Spotify URL**:
```text
Enter Spotify URL or Song Name (or 'q' to quit): Shape of You
```

### 2. Direct Search from Terminal
```powershell
# Search interactively (shows top matches to choose from)
python main.py "The Weeknd Blinding Lights"

# Search and automatically download the top match (-y)
python main.py "Coldplay Viva La Vida" -y
```

### 3. Direct Spotify URL Downloads

```powershell
# Single song
python main.py https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT

# Entire Album
python main.py https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy

# Playlist
python main.py https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
```

### 4. Custom Output Directory & Audio Quality

```powershell
# Custom destination folder
python main.py "Daft Punk Get Lucky" -o "C:\Music\Favorites" -y

# Download as FLAC Lossless
python main.py "Adele Easy On Me" -f flac -y

# Download as M4A
python main.py "Queen Bohemian Rhapsody" -f m4a -y

# Custom MP3 bitrate (256kbps)
python main.py "Taylor Swift Cruel Summer" -b 256 -y
```

---

## ⚙️ CLI Options & Flags

| Flag | Default | Description |
|---|---|---|
| `query` | *None* | Spotify URL OR song name/artist to search |
| `-y`, `--yes` | `False` | Auto-confirm and download the top search match without prompting |
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
│   ├── search.py           # iTunes & YouTube song search engine
│   ├── spotify.py          # Spotify metadata scraping & URL resolver
│   ├── downloader.py       # Audio search, download & FFmpeg conversion pipeline
│   ├── tagger.py           # Mutagen ID3/M4A/FLAC tag & album art embedder
│   └── cli.py              # Rich terminal interface & progress display
├── main.py                 # Main CLI executable
├── run.bat                 # Convenient Windows 1-click launcher
├── requirements.txt        # Package dependencies
└── README.md               # Documentation
```
