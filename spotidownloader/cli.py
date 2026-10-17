import os
import sys
import argparse
from typing import Optional, List

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

from .spotify import SpotifyClient, parse_spotify_url
from .downloader import AudioDownloader
from .search import search_songs
from .models import CollectionInfo, TrackInfo

# Support both UTF-8 terminals and Windows console
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

def format_duration(duration_ms: int) -> str:
    if not duration_ms:
        return "--:--"
    total_seconds = int(duration_ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"

def download_tracks_batch(
    tracks: List[TrackInfo],
    collection_title: str,
    output_dir: str = "downloads",
    audio_format: str = "mp3",
    bitrate: str = "320",
    skip_existing: bool = True,
    subfolder: Optional[str] = None,
):
    downloader = AudioDownloader(
        output_dir=output_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        skip_existing=skip_existing,
    )

    results = []
    total_tracks = len(tracks)

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
            f"[bold green]Overall Progress ({total_tracks} tracks)",
            total=total_tracks
        )
        current_task = progress.add_task("[cyan]Ready", total=100, visible=False)

        for idx, track in enumerate(tracks, start=1):
            track_label = f"({idx}/{total_tracks}) {track.artist_str} - {track.title}"
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

def handle_search_query(
    query: str,
    output_dir: str = "downloads",
    audio_format: str = "mp3",
    bitrate: str = "320",
    skip_existing: bool = True,
    auto_confirm: bool = False,
):
    with console.status(f"[bold cyan]Searching for '[white]{query}[/white]'...", spinner="dots"):
        search_results = search_songs(query, limit=5)

    if not search_results:
        console.print(f"[bold yellow]No songs found for query:[/bold yellow] '{query}'")
        return

    if auto_confirm:
        selected_tracks = [search_results[0]]
    else:
        # Show interactive selection table
        table = Table(title=f"[bold green]Search Results for: '{query}'[/bold green]", border_style="cyan")
        table.add_column("#", justify="center", style="bold cyan")
        table.add_column("Title", style="bold white")
        table.add_column("Artist", style="yellow")
        table.add_column("Album", style="dim")
        table.add_column("Duration", justify="center", style="green")

        for idx, t in enumerate(search_results, start=1):
            table.add_row(
                str(idx),
                t.title[:35],
                t.artist_str[:25],
                t.album[:25],
                format_duration(t.duration_ms),
            )

        console.print(table)

        choice = Prompt.ask(
            "[bold cyan]Select song number to download[/bold cyan] (1-5, 'all', or 'c' to cancel)",
            default="1"
        ).strip().lower()

        if choice in ('c', 'cancel', 'q', 'quit'):
            console.print("[dim]Search cancelled.[/dim]")
            return
        elif choice == 'all':
            selected_tracks = search_results
        else:
            try:
                # Support single number or comma separated e.g. "1, 3"
                parts = [int(p.strip()) for p in choice.split(',') if p.strip().isdigit()]
                selected_tracks = [search_results[p - 1] for p in parts if 1 <= p <= len(search_results)]
                if not selected_tracks:
                    selected_tracks = [search_results[0]]
            except Exception:
                selected_tracks = [search_results[0]]

    console.print(f"[bold green]Selected {len(selected_tracks)} song(s) for download.[/bold green]")
    download_tracks_batch(
        tracks=selected_tracks,
        collection_title=query,
        output_dir=output_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        skip_existing=skip_existing,
        subfolder=None,
    )

def handle_spotify_url(
    url: str,
    output_dir: str = "downloads",
    audio_format: str = "mp3",
    bitrate: str = "320",
    skip_existing: bool = True,
    create_subfolder: bool = True,
):
    spotify = SpotifyClient()

    with console.status("[bold cyan]Fetching Spotify metadata...", spinner="dots"):
        try:
            collection: CollectionInfo = spotify.fetch(url)
        except Exception as e:
            console.print(f"[bold red]Error fetching Spotify URL:[/bold red] {e}")
            return

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

    subfolder = None
    if create_subfolder and collection.type in ("album", "playlist", "artist"):
        safe_name = "".join(c for c in collection.title if c not in r'\/:*?"<>|').strip()
        subfolder = safe_name

    download_tracks_batch(
        tracks=collection.tracks,
        collection_title=collection.title,
        output_dir=output_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        skip_existing=skip_existing,
        subfolder=subfolder,
    )

def process_input(
    input_str: str,
    output_dir: str = "downloads",
    audio_format: str = "mp3",
    bitrate: str = "320",
    skip_existing: bool = True,
    create_subfolder: bool = True,
    auto_confirm: bool = False,
):
    input_str = input_str.strip()
    if parse_spotify_url(input_str):
        handle_spotify_url(
            url=input_str,
            output_dir=output_dir,
            audio_format=audio_format,
            bitrate=bitrate,
            skip_existing=skip_existing,
            create_subfolder=create_subfolder,
        )
    else:
        handle_search_query(
            query=input_str,
            output_dir=output_dir,
            audio_format=audio_format,
            bitrate=bitrate,
            skip_existing=skip_existing,
            auto_confirm=auto_confirm,
        )

def main():
    parser = argparse.ArgumentParser(
        description="SpotiDownloader - High quality Spotify Downloader & Search CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("query", nargs="*", help="Spotify URL (track, album, playlist) OR song name to search")
    parser.add_argument("-o", "--output", default="downloads", help="Directory where songs will be saved")
    parser.add_argument("-f", "--format", choices=["mp3", "m4a", "flac"], default="mp3", help="Audio format")
    parser.add_argument("-b", "--bitrate", choices=["320", "256", "192", "128"], default="320", help="Audio bitrate (kbps)")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm top search result without interactive prompt")
    parser.add_argument("--no-skip", action="store_true", help="Do not skip already existing files")
    parser.add_argument("--no-subfolder", action="store_true", help="Do not create a subfolder for albums/playlists")

    args = parser.parse_args()

    console.print(BANNER)

    raw_query = " ".join(args.query).strip() if args.query else ""

    if raw_query:
        process_input(
            input_str=raw_query,
            output_dir=args.output,
            audio_format=args.format,
            bitrate=args.bitrate,
            skip_existing=not args.no_skip,
            create_subfolder=not args.no_subfolder,
            auto_confirm=args.yes,
        )
    else:
        # Interactive mode
        console.print("[bold yellow]Interactive Mode[/bold yellow]")
        console.print("[dim]Tip: You can paste a Spotify URL OR type any song name to search![/dim]\n")
        while True:
            user_input = Prompt.ask("[bold cyan]Enter Spotify URL or Song Name[/bold cyan] (or 'q' to quit)").strip()
            if not user_input or user_input.lower() in ('q', 'quit', 'exit'):
                console.print("[dim]Goodbye![/dim]")
                break

            process_input(
                input_str=user_input,
                output_dir=args.output,
                audio_format=args.format,
                bitrate=args.bitrate,
                skip_existing=not args.no_skip,
                create_subfolder=not args.no_subfolder,
                auto_confirm=False,
            )

            if not Confirm.ask("\n[bold cyan]Would you like to search/download another song?[/bold cyan]", default=True):
                console.print("[dim]Goodbye![/dim]")
                break

if __name__ == "__main__":
    main()
