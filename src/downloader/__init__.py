"""
Downloader module.

Downloads videos from URLs using yt-dlp with Playwright MCP fallback.
"""

from src.downloader.downloader import download_videos, DownloadResult

__all__ = ["download_videos", "DownloadResult"]
