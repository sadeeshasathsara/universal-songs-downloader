import os
import sys
import argparse
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TaskID,
)
from rich.prompt import Prompt, Confirm

from .spotify import SpotifyClient
from .downloader import AudioDownloader
from .models import CollectionInfo, TrackInfo

# Support both UTF-8 terminals and Windows cp1252 fallbacks
console = Console(highlight=False)


BANNER = r"""[bold green]
   _____             _   _ _____                      _                 _           
  / ____|           | | (_)  __ \                    | |               | |          
 | (___  _ __   ___ | |_ _| |  | | _____      ___ __ | | ___   __ _  __| | ___ _ __ 
  \___ \| '_ \ / _ \| __| | |  | |/ _ \ \ /\ / / '_ \| |/ _ \ / _` |/ _` |/ _ \ '__|
  ____) | |_) | (_) | |_| | |__| | (_) \ V  V /| | | | | (_) | (_| | (_| |  __/ |   
 |_____/| .__/ \___/ \__|_|_____/ \___/ \_/\_/ |_| |_|_|\___/ \__,_|\__,_|\___|_|   
        | |                                                                         
        |_|                                   [cyan]High Quality Spotify Downloader CLI[/cyan]
[/bold green]"""

def format_file_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def process_download(
    url: str,
    output_dir: str = "downloads",
    audio_format: str = "mp3",
    bitrate: str = "320",
    skip_existing: bool = True,
    create_subfolder: bool = True,
):
    spotify = SpotifyClient()
    downloader = AudioDownloader(
        output_dir=output_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        skip_existing=skip_existing,
    )

    with console.status("[bold cyan]Fetching Spotify metadata...", spinner="dots"):
        try:
            collection: CollectionInfo = spotify.fetch(url)
        except Exception as e:
            console.print(f"[bold red]Error fetching URL:[/bold red] {e}")
            return

    # Print metadata panel
    info_text = (
        f"[bold white]Title:[/bold white] {collection.title}\n"
        f"[bold white]Type:[/bold white] {collection.type.capitalize()}\n"
        f"[bold white]Total Tracks:[/bold white] {collection.total_count}\n"
        f"[bold white]Output Dir:[/bold white] [underline]{os.path.abspath(output_dir)}[/underline]\n"
        f"[bold white]Quality:[/bold white] {audio_format.upper()} @ {bitrate}kbps"
    )
    console.print(Panel(info_text, title="[bold green]Spotify Metadata Found[/bold green]", border_style="green"))

    if not collection.tracks:
        console.print("[yellow]No tracks found in the specified collection.[/yellow]")
        return

    # Subfolder organization
    subfolder = None
    if create_subfolder and collection.type in ("album", "playlist", "artist"):
        safe_name = "".join(c for c in collection.title if c not in r'\/:*?"<>|').strip()
        subfolder = safe_name

    results = []

    # Rich multi-progress display
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        overall_task = progress.add_task(
            f"[bold green]Overall Progress ({collection.total_count} tracks)",
            total=collection.total_count
        )
        current_task = progress.add_task("[cyan]Ready", total=100, visible=False)

        for idx, track in enumerate(collection.tracks, start=1):
            track_label = f"({idx}/{collection.total_count}) {track.artist_str} - {track.title}"
            progress.update(
                current_task,
                description=f"[cyan]Downloading: {track_label[:45]}...",
                completed=0,
                total=100,
                visible=True
            )

            def ytdl_progress_hook(d):
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    downloaded = d.get('downloaded_bytes') or 0
                    if total > 0:
                        progress.update(
                            current_task,
                            completed=downloaded,
                            total=total
                        )
                elif d['status'] == 'finished':
                    progress.update(
                        current_task,
                        description=f"[yellow]Converting & Tagging: {track_label[:40]}...",
                    )

            success, filepath, status = downloader.download_track(
                track=track,
                subfolder=subfolder,
                progress_callback=ytdl_progress_hook,
            )

            file_size_str = "-"
            if os.path.exists(filepath):
                file_size_str = format_file_size(os.path.getsize(filepath))

            results.append({
                "num": idx,
                "title": f"{track.artist_str} - {track.title}",
                "success": success,
                "status": status,
                "filepath": filepath,
                "size": file_size_str,
            })

            progress.advance(overall_task)

        progress.update(current_task, visible=False)

    # Print summary table
    table = Table(title="[bold green]Download Summary[/bold green]", border_style="cyan")
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Track", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Saved To", style="dim")

    downloaded_count = 0
    skipped_count = 0
    failed_count = 0

    for r in results:
        if r["status"] == "downloaded":
            status_badge = "[bold green][DONE][/bold green]"
            downloaded_count += 1
        elif r["status"] == "already_exists":
            status_badge = "[bold yellow][EXISTS][/bold yellow]"
            skipped_count += 1
        else:
            status_badge = f"[bold red][FAIL] {r['status'][:15]}[/bold red]"
            failed_count += 1

        rel_path = os.path.relpath(r["filepath"]) if os.path.isabs(r["filepath"]) else r["filepath"]
        table.add_row(
            str(r["num"]),
            r["title"][:50],
            status_badge,
            r["size"],
            rel_path[:40]
        )

    console.print(table)
    console.print(
        f"\n[bold]Completed:[/bold] [green]{downloaded_count} downloaded[/green], "
        f"[yellow]{skipped_count} skipped[/yellow], "
        f"[red]{failed_count} failed[/red].\n"
    )

def main():
    parser = argparse.ArgumentParser(
        description="SpotiDownloader - High quality Spotify Downloader CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="Spotify track, album, or playlist URL")
    parser.add_argument("-o", "--output", default="downloads", help="Directory where songs will be saved")
    parser.add_argument("-f", "--format", choices=["mp3", "m4a", "flac"], default="mp3", help="Audio format")
    parser.add_argument("-b", "--bitrate", choices=["320", "256", "192", "128"], default="320", help="Audio bitrate (kbps)")
    parser.add_argument("--no-skip", action="store_true", help="Do not skip already existing files")
    parser.add_argument("--no-subfolder", action="store_true", help="Do not create a subfolder for albums/playlists")

    args = parser.parse_args()

    console.print(BANNER)

    if args.url:
        process_download(
            url=args.url,
            output_dir=args.output,
            audio_format=args.format,
            bitrate=args.bitrate,
            skip_existing=not args.no_skip,
            create_subfolder=not args.no_subfolder,
        )
    else:
        # Interactive mode
        console.print("[bold yellow]Interactive Mode[/bold yellow]\n")
        while True:
            url = Prompt.ask("[bold cyan]Enter Spotify Track, Album, or Playlist URL[/bold cyan] (or 'q' to quit)")
            if not url or url.lower() in ('q', 'quit', 'exit'):
                console.print("[dim]Goodbye![/dim]")
                break

            process_download(
                url=url,
                output_dir=args.output,
                audio_format=args.format,
                bitrate=args.bitrate,
                skip_existing=not args.no_skip,
                create_subfolder=not args.no_subfolder,
            )

            if not Confirm.ask("\n[bold cyan]Would you like to download another URL?[/bold cyan]", default=True):
                console.print("[dim]Goodbye![/dim]")
                break

if __name__ == "__main__":
    main()
