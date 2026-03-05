"""
Main downloader module.

Orchestrates yt-dlp with Playwright MCP fallback.
"""

import logging
import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession
from mcp.client.sse import sse_client

from src.downloader.validator import validate_urls, ValidationError
from src.downloader.ytdlp import (
    download as ytdlp_download,
    download_stream,
    DownloadOptions,
    YtdlpError,
    AuthenticationError,
    UnsupportedURLError,
)
from src.downloader.agent import BrowserAgent, VideoInfo
from src.downloader.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """Result of download operation."""
    total: int = 0
    successful: int = 0
    downloaded: list[Path] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def add_failure(self, url: str, error: str) -> None:
        """Record failed download."""
        self.failed.append((url, error))

    def summary(self) -> str:
        lines = [
            f"Downloaded: {len(self.downloaded)}/{self.total} videos"
        ]

        if self.downloaded:
            lines.append("\nDownloaded files:")
            for path in self.downloaded:
                lines.append(f"\t{path}")

        if self.failed:
            lines.append("\nFailed URLs:")
            for url, error in self.failed:
                lines.append(f"\t{url}: {error}")

        return '\n'.join(lines)


def _download_with_ytdlp(url: str, options: DownloadOptions) -> Path:
    """Try to download with yt-dlp."""
    return ytdlp_download(url, options)


async def _download_with_playwright(
    urls: list[str],
    output_dir: Path,
    username: str | None,
    password: str | None,
    mcp_url: str,
    model: str,
    max_login_steps: int,
    max_extract_steps: int,
) -> list[tuple[str, Path | None, str | None]]:
    """
    Download using Playwright MCP browser agent.

    Flow:
    1. Connect to Playwright MCP
    2. Extract video URLs and cookies from browser
    3. Download videos using yt-dlp with extracted cookies

    Returns list of (url, path, error) tuples.
    """
    results: list[tuple[str, Path | None, str | None]] = []

    async with sse_client(f"{mcp_url}/sse") as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            logger.info(f"Connected to Playwright MCP at {mcp_url}")

            agent = BrowserAgent(
                session,
                model=model,
                max_login_steps=max_login_steps,
                max_extract_steps=max_extract_steps,
            )

            # Extract all videos (session persists for auth)
            video_infos = await agent.extract_videos(urls, username, password)

            # Download each found video
            for info in video_infos:
                if info.error:
                    results.append((info.page_url, None, info.error))
                    continue

                if not info.video_url:
                    results.append((info.page_url, None, "No video URL found"))
                    continue

                # Download the video using yt-dlp with cookies from browser
                try:
                    logger.info(f"Downloading: {info.video_url[:80]}...")
                    if info.cookies:
                        logger.info("Using cookies from browser session")

                    # Generate filename from title or URL
                    filename = _sanitize_filename(info.title or info.page_url.split("/")[-1])

                    # Use download_stream with cookies for authenticated download
                    path = download_stream(
                        video_url=info.video_url,
                        output_dir=output_dir,
                        filename=filename,
                        cookies_str=info.cookies,
                    )
                    results.append((info.page_url, path, None))

                except Exception as e:
                    results.append((info.page_url, None, f"Download failed: {e}"))

    return results


def _sanitize_filename(name: str) -> str:
    """Convert to safe filename."""
    import re
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:100]


def download_videos(
    urls: list[str],
    output_dir: Path,
    username: str | None = None,
    password: str | None = None,
    cookies_file: Path | None = None,
) -> DownloadResult:
    """
    Download multiple videos.

    Args:
        urls: List of video URLs (must be from same website)
        output_dir: Output directory
        username: Optional username for authentication
        password: Optional password for authentication
        cookies_file: Optional path to cookies file

    Returns:
        DownloadResult with status and file paths
    """

    result = DownloadResult(total=len(urls))
    settings = get_settings()

    # Validate URLs
    try:
        validated_urls = validate_urls(urls)
    except ValidationError as e:
        logger.error(f"URL validation failed: {e}")
        for url in urls:
            result.add_failure(url, str(e))
        return result

    # Track URLs that need Playwright fallback
    fallback_urls: list[str] = []

    # First pass: try yt-dlp for each URL
    for i, url in enumerate(validated_urls, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {i}/{len(validated_urls)}: {url}")
        logger.info(f"{'='*60}")

        options = DownloadOptions(
            output_dir=output_dir,
            username=username,
            password=password,
            cookies_file=cookies_file,
        )

        try:
            path = _download_with_ytdlp(url, options)
            result.downloaded.append(path)
            logger.info(f"[SUCCESS] Downloaded: {path}")

        except (AuthenticationError, UnsupportedURLError, YtdlpError) as e:
            logger.warning(f"[yt-dlp] {e} - will try Playwright")
            fallback_urls.append(url)

    # Second pass: use Playwright for failed URLs
    if fallback_urls:
        logger.info(f"\n{'='*60}")
        logger.info(f"Playwright fallback for {len(fallback_urls)} URLs")
        logger.info(f"{'='*60}")

        try:
            playwright_results = asyncio.run(_download_with_playwright(
                urls=fallback_urls,
                output_dir=output_dir,
                username=username,
                password=password,
                mcp_url=settings.mcp_url,
                model=settings.agent_model,
                max_login_steps=settings.agent_max_login_steps,
                max_extract_steps=settings.agent_max_extract_steps,
            ))

            for url, path, error in playwright_results:
                if path:
                    result.downloaded.append(path)
                    logger.info(f"[SUCCESS] Downloaded: {path}")
                else:
                    result.add_failure(url, error or "Unknown error")
                    logger.error(f"[FAILED] {url}: {error}")

        except Exception as e:
            logger.error(f"Playwright fallback failed: {e}")
            for url in fallback_urls:
                result.add_failure(url, f"Playwright error: {e}")

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Download complete: {result.successful}/{result.total} successful")
    logger.info(f"{'='*60}")

    return result
