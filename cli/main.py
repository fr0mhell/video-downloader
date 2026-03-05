"""
video-loader CLI

Downloads videos from URLs with optional authentication.
"""

import logging
from pathlib import Path

import typer

from src.downloader import download_videos, DownloadResult

app = typer.Typer(
    name="video-loader",
    help="Download videos from URLs with optional authentication",
)


def setup_logging(verbose: bool) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s - %(message)s",
    )


@app.command()
def main(
    urls: list[str] = typer.Argument(
        None,
        help="Video URLs to download",
    ),
    url_file: Path | None = typer.Option(
        None,
        "--url-file", "-f",
        help="Text file with URLs (one per line)",
    ),
    login: str | None = typer.Option(
        None,
        "--login", "-l",
        help="Username or email for authentication",
    ),
    password: str | None = typer.Option(
        None,
        "--password", "-p",
        help="Password for authentication",
    ),
    output: Path = typer.Option(
        Path("."),
        "--output", "-o",
        help="Output directory for downloaded videos",
    ),
    cookies: Path | None = typer.Option(
        None,
        "--cookies", "-c",
        help="Path to cookies file (Netscape format)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Enable verbose logging",
    ),
) -> None:
    """
    Download videos from URLs.

    Provide URLs as arguments or via a text file (one URL per line).

    Examples:
        video-loader https://example.com/video1 https://example.com/video2
        video-loader -f urls.txt
        video-loader https://example.com/video --login user@example.com --password secret
        video-loader https://example.com/video --cookies cookies.txt
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # Collect URLs from arguments and/or file
    all_urls: list[str] = list(urls or [])

    if url_file:
        if not url_file.exists():
            typer.echo(f"Error: URL file not found: {url_file}", err=True)
            raise typer.Exit(1)
        lines = url_file.read_text().strip().splitlines()
        file_urls = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
        logger.info(f"Loaded {len(file_urls)} URLs from {url_file}")
        all_urls.extend(file_urls)

    if not all_urls:
        typer.echo("Error: provide URLs as arguments or via --url-file", err=True)
        raise typer.Exit(1)

    urls = all_urls

    logger.info(f"Processing {len(urls)} URLs")
    logger.info(f"Output directory: {output}")

    if login and password:
        logger.info(f"Using credentials for: {login}")
    elif cookies:
        logger.info(f"Using cookies file: {cookies}")

    # Download videos
    result: DownloadResult = download_videos(
        urls=urls,
        output_dir=output,
        username=login,
        password=password,
        cookies_file=cookies,
    )

    # Print summary
    typer.echo(result.summary())

    if result.failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
