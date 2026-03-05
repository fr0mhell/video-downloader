# video-loader

CLI tool for downloading videos from URLs. Uses **yt-dlp** as the primary download engine with an LLM-powered **Playwright MCP** browser agent as a fallback for sites that require authentication or are not natively supported by yt-dlp.

## How it works

1. Validate that all provided URLs belong to the same website
2. Attempt to download each video using yt-dlp (supports credentials and cookies)
3. For any URL that fails (auth required, unsupported site), fall back to the Playwright browser agent:
   - Navigate to the page in a headless browser
   - Detect login pages and authenticate using an LLM-guided flow
   - Extract video stream URLs from network requests or page content
   - Download the extracted streams with yt-dlp using session cookies

## Quick start (Docker)

```bash
cp .env.example .env
# Fill in at least one LLM API key (OPENAI_API_KEY or GOOGLE_API_KEY)

# Start services (playwright-mcp needs ~60s to become healthy)
docker compose up -d

# Download videos
docker compose run downloader https://youtube.com/watch?v=VIDEO_ID

# Download from a list of URLs in a file
docker compose run downloader -f /downloads/urls.txt

# With authentication
docker compose run downloader https://example.com/video -l user@example.com -p secret

# With cookies file (place it in ./cookies/)
docker compose run downloader https://example.com/video -c /cookies/cookies.txt

# Custom output directory
docker compose run downloader https://example.com/video -o /downloads/my-course
```

Downloaded files are saved to the `./downloads/` directory on the host.

## Local installation

Requires Python 3.12+.

```bash
pip install -e .

video-loader https://youtube.com/watch?v=VIDEO_ID
video-loader -f urls.txt -o ./output
video-loader https://example.com/video -o ./output -v
```

For the Playwright fallback to work locally, you need a running Playwright MCP server:

```bash
npx -y @playwright/mcp@latest --browser firefox --port 3000 --headless
```

## CLI options

```
video-loader [OPTIONS] [URLS]...

Arguments:
  URLS                  Video URLs to download (must be from the same website)

Options:
  -f, --url-file PATH   Text file with URLs (one per line)
  -l, --login TEXT      Username or email for authentication
  -p, --password TEXT   Password for authentication
  -o, --output PATH     Output directory (default: current directory)
  -c, --cookies PATH    Path to cookies file (Netscape format)
  -v, --verbose         Enable debug logging
  --help                Show help message
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_URL` | `http://localhost:3000` | Playwright MCP server URL |
| `AGENT_MODEL` | `gpt-4.1-mini` | LLM model for the browser agent |
| `OPENAI_API_KEY` | — | OpenAI API key (for gpt-* models) |
| `GOOGLE_API_KEY` | — | Google API key (for gemini-* models) |
| `AGENT_MAX_LOGIN_STEPS` | `15` | Max LLM steps for login flow |
| `AGENT_MAX_EXTRACT_STEPS` | `10` | Max LLM steps for video extraction |

## Project structure

```
loader/
├── cli/main.py                  # Typer CLI entrypoint
├── src/downloader/
│   ├── downloader.py            # Orchestrator (yt-dlp + Playwright fallback)
│   ├── ytdlp.py                 # yt-dlp wrapper
│   ├── agent.py                 # LLM-powered Playwright browser agent
│   ├── llm.py                   # LiteLLM client
│   ├── validator.py             # URL validation
│   └── config.py                # Pydantic settings
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```
