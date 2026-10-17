#!/usr/bin/env python3
"""
Entry point to run the Universal Songs Downloader Telegram Bot
"""
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from spotidownloader.bot import main

if __name__ == "__main__":
    main()
