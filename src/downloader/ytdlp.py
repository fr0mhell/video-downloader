"""
yt-dlp wrapper for video downloading.

Uses yt-dlp as a Python library (not CLI subprocess).
Supports cookies from browser agent for authenticated downloads.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)


class YtdlpError(Exception):
    """yt-dlp download error."""
    pass


class AuthenticationError(YtdlpError):
    """Authentication required or failed."""
    pass


class UnsupportedURLError(YtdlpError):
    """URL not supported by yt-dlp."""
    pass


@dataclass
class DownloadOptions:
    """Download options."""
    output_dir: Path
    username: str | None = None
    password: str | None = None
    cookies_file: Path | None = None
    cookies_str: str | None = None  # Cookies from browser agent
    format: str = "best"


def _parse_error(error: Exception, url: str) -> YtdlpError:
    """Parse yt-dlp error and return appropriate exception."""
    error_str = str(error).lower()

    if any(w in error_str for w in ["login", "sign in", "authentication", "unauthorized", "403"]):
        return AuthenticationError(f"Authentication required: {url}")

    if "unsupported url" in error_str:
        return UnsupportedURLError(f"URL not supported: {url}")

    if "login" in error_str and "redirect" in error_str:
        return AuthenticationError(f"Redirected to login: {url}")

    return YtdlpError(f"Download failed: {error}")


def _write_cookies_file(cookies_str: str, output_dir: Path) -> Path:
    """Write cookies string to Netscape cookies file format."""
    cookies_file = output_dir / ".cookies.txt"

    # Convert simple cookie string to Netscape format
    lines = ["# Netscape HTTP Cookie File"]

    for cookie in cookies_str.split(";"):
        cookie = cookie.strip()
        if "=" in cookie:
            name, value = cookie.split("=", 1)
            # Format: domain, flag, path, secure, expiration, name, value
            lines.append(f".example.com\tTRUE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}")

    cookies_file.write_text("\n".join(lines))
    return cookies_file


def download(url: str, options: DownloadOptions) -> Path:
    """
    Download video using yt-dlp Python library.

    Args:
        url: Video page URL or direct video URL
        options: Download options including auth

    Returns:
        Path to downloaded file
    """
    logger.info(f"yt-dlp downloading: {url}")
    options.output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "outtmpl": str(options.output_dir / "%(title).100s.%(ext)s"),
        "format": options.format,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "extract_flat": False,
    }

    # Authentication - priority: cookies_file > cookies_str > username/password
    cookies_file_to_delete = None

    if options.cookies_file and options.cookies_file.exists():
        ydl_opts["cookiefile"] = str(options.cookies_file)
        logger.info(f"Using cookies file: {options.cookies_file}")
    elif options.cookies_str:
        # Write cookies string to temp file
        cookies_file_to_delete = _write_cookies_file(options.cookies_str, options.output_dir)
        ydl_opts["cookiefile"] = str(cookies_file_to_delete)
        logger.info("Using cookies from browser agent")
    elif options.username and options.password:
        ydl_opts["username"] = options.username
        ydl_opts["password"] = options.password
        logger.info(f"Using credentials: {options.username}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if not info:
                raise YtdlpError(f"No info extracted: {url}")

            # Get filename
            filename = ydl.prepare_filename(info)
            path = Path(filename)

            # Handle merged output (yt-dlp might add .mp4)
            if not path.exists():
                path = path.with_suffix(".mp4")

            if not path.exists():
                # Find most recent file in output dir
                files = list(options.output_dir.glob("*.*"))
                video_files = [f for f in files if f.suffix in ['.mp4', '.mkv', '.webm', '.m4v']]
                if video_files:
                    path = max(video_files, key=lambda p: p.stat().st_mtime)

            if path.exists():
                size_mb = path.stat().st_size / 1024 / 1024
                logger.info(f"Downloaded: {path.name} ({size_mb:.1f} MB)")
                return path

            raise YtdlpError(f"File not found after download: {filename}")

    except yt_dlp.utils.DownloadError as e:
        raise _parse_error(e, url)

    finally:
        # Clean up temp cookies file
        if cookies_file_to_delete and cookies_file_to_delete.exists():
            cookies_file_to_delete.unlink()


async def download_async(url: str, options: DownloadOptions) -> Path:
    """Async wrapper for download function."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: download(url, options))


def download_stream(
    video_url: str,
    output_dir: Path,
    filename: str | None = None,
    cookies_str: str | None = None,
) -> Path:
    """
    Download video stream (m3u8, mpd, mp4) using yt-dlp.

    Args:
        video_url: Direct video URL (m3u8, mp4, etc.)
        output_dir: Output directory
        filename: Optional filename (without extension)
        cookies_str: Optional cookies from browser

    Returns:
        Path to downloaded file
    """
    logger.info(f"Downloading stream: {video_url[:80]}...")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate output template
    if filename:
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)[:100]
        outtmpl = str(output_dir / f"{safe_name}.%(ext)s")
    else:
        outtmpl = str(output_dir / "%(title).100s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "best",
        "merge_output_format": "mp4",
        "quiet": False,
    }

    # Add cookies if provided
    cookies_file = None
    if cookies_str:
        cookies_file = _write_cookies_file(cookies_str, output_dir)
        ydl_opts["cookiefile"] = str(cookies_file)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)

            if not info:
                raise YtdlpError("No info extracted")

            filename = ydl.prepare_filename(info)
            path = Path(filename)

            # Handle merged output
            if not path.exists():
                path = path.with_suffix(".mp4")

            if not path.exists():
                # Find most recent file
                files = list(output_dir.glob("*.*"))
                video_files = [f for f in files if f.suffix in ['.mp4', '.mkv', '.webm']]
                if video_files:
                    path = max(video_files, key=lambda p: p.stat().st_mtime)

            if path.exists():
                size_mb = path.stat().st_size / 1024 / 1024
                logger.info(f"Downloaded: {path.name} ({size_mb:.1f} MB)")
                return path

            raise YtdlpError(f"File not found: {filename}")

    finally:
        if cookies_file and cookies_file.exists():
            cookies_file.unlink()


async def download_stream_async(
    video_url: str,
    output_dir: Path,
    filename: str | None = None,
    cookies_str: str | None = None,
) -> Path:
    """Async wrapper for download_stream."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: download_stream(video_url, output_dir, filename, cookies_str)
    )
