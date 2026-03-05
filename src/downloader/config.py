"""
Downloader configuration.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DownloaderSettings(BaseSettings):
    """Downloader configuration from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Playwright MCP
    mcp_url: str = "http://localhost:3000"

    # LLM settings
    agent_model: str = "gpt-4o-mini"

    # API Keys (optional, litellm reads them directly too)
    openai_api_key: str | None = None
    google_api_key: str | None = None

    # Authentication
    cookies_file: Path | None = None

    # Download settings
    download_dir: Path = Path("/downloads")

    # Agent settings
    agent_max_login_steps: int = 15
    agent_max_extract_steps: int = 10


def get_settings() -> DownloaderSettings:
    """Get downloader settings."""
    return DownloaderSettings()
