"""
Browser agent for video extraction via Playwright MCP.

Fallback process (iteration 2):
1. Open URL with httpx, catch redirect to login page
2. Get HTML, compress it, use LLM to detect if it's login page
3. If not login - raise error
4. If login - use LLM to find xpath for email, password, submit
5. Use LLM + Playwright MCP to enter credentials
6. Save browser session and use for all URLs
7. Find video on page, download with yt-dlp using cookies
"""

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx
from mcp import ClientSession

from src.downloader.llm import LLMClient

logger = logging.getLogger(__name__)


# System prompt for login page detection
LOGIN_DETECTION_PROMPT = """You are analyzing a web page to determine if it is a login page.

Analyze the provided HTML and respond with JSON:
{
    "is_login_page": true/false,
    "reason": "explanation of why this is or isn't a login page",
    "email_xpath": "xpath to email/username input field" or null,
    "password_xpath": "xpath to password input field" or null,
    "submit_xpath": "xpath to login/submit button" or null
}

A login page typically has:
- An input field for email/username
- An input field for password (type="password")
- A submit/login button

Look for common patterns in different languages (English, Russian, etc):
- Login, Sign in, Войти, Вход
- Email, Username, Логин, Почта
- Password, Пароль

Provide reliable xpaths that will work for form submission."""


# System prompt for browser actions
BROWSER_ACTION_PROMPT = """You are a browser automation agent. Execute actions to complete tasks.

Available browser tools:
- browser_navigate: Navigate to URL. Args: {"url": "https://..."}
- browser_click: Click element. Args: {"element": "description", "ref": "e27"}
- browser_type: Type text. Args: {"element": "description", "ref": "e21", "text": "value", "submit": false}
- browser_snapshot: Get page accessibility snapshot (no args)
- browser_wait_for: Wait seconds. Args: {"time": 2}
- browser_press_key: Press keyboard key. Args: {"key": "Enter"}

Page snapshots show elements with [ref=eN] references.
ALWAYS use both "element" (description) and "ref" for click/type actions.

Respond with JSON:
{
    "thought": "your reasoning about what to do next",
    "action": "tool_name" or "done" or "error",
    "args": {...},
    "result": "final result when action is done"
}"""


VIDEO_EXTRACTION_PROMPT = """You are a video extraction agent. Find video download URLs on web pages.

Available browser tools:
- browser_navigate: Navigate to URL. Args: {"url": "https://..."}
- browser_click: Click element. Args: {"element": "button description", "ref": "e27"}
- browser_snapshot: Get page accessibility snapshot (no args)
- browser_wait_for: Wait seconds. Args: {"time": 2}

Page snapshots show elements with [ref=eN] references.

Respond with JSON:
{
    "thought": "reasoning",
    "action": "tool_name" or "done" or "error",
    "args": {"element": "...", "ref": "eN", ...},
    "result": "video URL when action is done"
}

Video URL patterns: .mp4, .webm, .m3u8, .mpd
Look for video elements, download buttons, or quality selectors."""


class HTMLCompressor(HTMLParser):
    """Compress HTML by extracting only relevant elements for login detection."""

    def __init__(self):
        super().__init__()
        self.result = []
        self.relevant_tags = {'form', 'input', 'button', 'a', 'label', 'h1', 'h2', 'title', 'div'}
        self.self_closing_tags = {'input', 'img', 'br', 'hr'}
        self.in_form = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # Always capture form and its contents
        if tag == 'form':
            self.in_form = True
            attrs_str = ' '.join(f'{k}="{v}"' for k, v in attrs if v)
            self.result.append(f'<form {attrs_str}>')
            return

        # Inside form - capture everything relevant
        if self.in_form:
            if tag in ['input', 'button', 'select', 'textarea', 'label']:
                attrs_str = ' '.join(f'{k}="{v}"' for k, v in attrs if v)
                if tag in self.self_closing_tags:
                    self.result.append(f'<{tag} {attrs_str}/>')
                else:
                    self.result.append(f'<{tag} {attrs_str}>')
            return

        # Outside form - capture standalone inputs and buttons
        if tag in ['input', 'button']:
            attrs_str = ' '.join(f'{k}="{v}"' for k, v in attrs if v)
            if tag in self.self_closing_tags:
                self.result.append(f'<{tag} {attrs_str}/>')
            else:
                self.result.append(f'<{tag} {attrs_str}>')

        # Capture titles and headings
        if tag in ['title', 'h1', 'h2']:
            self.result.append(f'<{tag}>')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.in_form = False
            self.result.append('</form>')
        elif self.in_form and tag in ['button', 'select', 'textarea', 'label']:
            self.result.append(f'</{tag}>')
        elif tag in ['title', 'h1', 'h2', 'button']:
            self.result.append(f'</{tag}>')

    def handle_data(self, data):
        data = data.strip()
        if data and len(data) < 100:  # Avoid very long text
            self.result.append(data)

    def get_compressed(self) -> str:
        return '\n'.join(self.result)


def compress_html(html: str) -> str:
    """Compress HTML to relevant elements only."""
    result_parts = []

    # Method 1: Try HTML parser
    parser = HTMLCompressor()
    try:
        parser.feed(html)
        parsed = parser.get_compressed()
        if parsed:
            result_parts.append(parsed)
    except Exception:
        pass

    # Method 2: Use regex to find input fields (backup)
    input_pattern = r'<input[^>]*>'
    inputs = re.findall(input_pattern, html, re.IGNORECASE)
    if inputs:
        result_parts.append("\n--- Input fields found by regex ---")
        result_parts.extend(inputs[:20])  # Limit to 20 inputs

    # Method 3: Find buttons
    button_pattern = r'<button[^>]*>.*?</button>'
    buttons = re.findall(button_pattern, html, re.IGNORECASE | re.DOTALL)
    if buttons:
        result_parts.append("\n--- Buttons found by regex ---")
        result_parts.extend(buttons[:10])

    # Method 4: Find form tags
    form_pattern = r'<form[^>]*>'
    forms = re.findall(form_pattern, html, re.IGNORECASE)
    if forms:
        result_parts.append("\n--- Forms found by regex ---")
        result_parts.extend(forms[:5])

    if result_parts:
        return '\n'.join(result_parts)

    # Fallback: return trimmed HTML
    return html[:8000]


@dataclass
class VideoInfo:
    """Extracted video information with session data for download."""
    page_url: str
    video_url: str | None = None
    title: str | None = None
    cookies: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass
class LoginInfo:
    """Login page analysis result."""
    is_login_page: bool
    redirect_url: str | None = None
    email_xpath: str | None = None
    password_xpath: str | None = None
    submit_xpath: str | None = None
    reason: str | None = None


def detect_login_redirect(url: str) -> tuple[str | None, str | None]:
    """
    Step 1: Open URL with httpx, catch redirect to login page.

    Returns: (redirect_url, html_content) or (None, None) if no redirect
    """
    logger.info(f"Checking URL for login redirect: {url}")

    try:
        with httpx.Client(follow_redirects=False, timeout=15) as client:
            response = client.get(url)

            # Check for redirect
            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get("location", "")
                if not redirect_url.startswith("http"):
                    # Handle relative URLs
                    from urllib.parse import urljoin
                    redirect_url = urljoin(url, redirect_url)

                logger.info(f"Redirect detected: {response.status_code} -> {redirect_url}")

                # Fetch the redirect target
                response2 = client.get(redirect_url, follow_redirects=True)
                return redirect_url, response2.text

            # No redirect but check if page has login form
            if response.status_code == 200:
                return url, response.text

    except Exception as e:
        logger.warning(f"httpx request failed: {e}")

    return None, None


class BrowserAgent:
    """LLM-powered browser agent for video extraction."""

    def __init__(
        self,
        session: ClientSession,
        model: str = "gpt-4o-mini",
        max_login_steps: int = 15,
        max_extract_steps: int = 10,
    ):
        self.session = session
        self.llm = LLMClient(model=model)
        self._authenticated = False
        self._cookies: str | None = None
        self._max_login_steps = max_login_steps
        self._max_extract_steps = max_extract_steps

    async def call_tool(self, name: str, args: dict | None = None) -> str:
        """Call MCP browser tool."""
        logger.debug(f"Tool: {name} args={args}")
        result = await self.session.call_tool(name, args or {})

        for content in result.content or []:
            if hasattr(content, 'text'):
                return content.text
        return ""

    async def get_snapshot(self) -> str:
        """Get page accessibility snapshot."""
        return await self.call_tool("browser_snapshot")

    async def get_cookies(self) -> str | None:
        """Extract cookies from browser for yt-dlp."""
        try:
            result = await self.call_tool("browser_evaluate", {
                "expression": "document.cookie"
            })
            if result and result != "No result" and len(result) > 5:
                logger.info(f"Extracted cookies: {len(result)} chars")
                return result
        except Exception as e:
            logger.debug(f"Cookie extraction failed: {e}")
        return None

    async def get_network_requests(self) -> str:
        """Get network requests to find video URLs."""
        try:
            return await self.call_tool("browser_network_requests", {})
        except Exception:
            return ""

    def _parse_url(self, snapshot: str) -> str:
        """Parse URL from snapshot."""
        for line in snapshot.split("\n"):
            if "- Page URL:" in line:
                return line.split(":", 1)[1].strip()
        return ""

    def _parse_title(self, snapshot: str) -> str:
        """Parse title from snapshot."""
        for line in snapshot.split("\n")[:15]:
            if "- Page Title:" in line:
                return line.split(":", 1)[1].strip()
        return ""

    def _find_video_in_network(self, network: str) -> str | None:
        """Find video URL in network requests."""
        patterns = [
            r'https?://[^\s\]"\']+\.m3u8[^\s\]"\']*',
            r'https?://[^\s\]"\']+\.mpd[^\s\]"\']*',
            r'https?://[^\s\]"\']+\.mp4[^\s\]"\']*',
            r'https?://[^\s\]"\']+/playlist/master/[^\s\]"\']+',
            r'https?://[^\s\]"\']+/hls/[^\s\]"\']+',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, network, re.IGNORECASE)
            if matches:
                url = matches[0].rstrip('"\'>,]')
                logger.info(f"Found video in network: {url[:80]}...")
                return url

        return None

    async def analyze_login_page(self, html: str) -> LoginInfo:
        """
        Step 2: Use LLM to analyze HTML and detect if it's a login page.
        Also extract xpaths for form fields.
        """
        compressed = compress_html(html)
        logger.info(f"Compressed HTML: {len(html)} -> {len(compressed)} chars")

        prompt = f"""Analyze this HTML to determine if it's a login page.

HTML content:
{compressed}

Respond with JSON containing:
- is_login_page: boolean
- reason: why you think this is or isn't a login page
- email_xpath: xpath to the email/username input (or null)
- password_xpath: xpath to the password input (or null)
- submit_xpath: xpath to the submit button (or null)"""

        response = await self.llm.complete(prompt, LOGIN_DETECTION_PROMPT, json_mode=True)

        # Log full response for debugging
        logger.info(f"LLM login detection response:\n{response.content}")

        result = self.llm.parse_json(response.content)

        return LoginInfo(
            is_login_page=result.get("is_login_page", False),
            email_xpath=result.get("email_xpath"),
            password_xpath=result.get("password_xpath"),
            submit_xpath=result.get("submit_xpath"),
            reason=result.get("reason"),
        )

    async def perform_login_with_llm(
        self,
        username: str,
        password: str,
        login_info: LoginInfo,
    ) -> bool:
        """
        Steps 4-5: Use LLM agent to enter credentials and login.
        """
        logger.info("Starting LLM-guided login process...")

        # Get current page snapshot
        snapshot = await self.get_snapshot()
        logger.info(f"Login page snapshot:\n{snapshot}")

        prompt = f"""You need to log into this page.

Credentials:
- Email/Username: {username}
- Password: {password}

Page snapshot:
{snapshot}

Hints from HTML analysis:
- Email field xpath: {login_info.email_xpath}
- Password field xpath: {login_info.password_xpath}
- Submit button xpath: {login_info.submit_xpath}

Steps:
1. Find the email/username input field and type the email
2. Find the password input field and type the password
3. Click the login/submit button
4. Return "done" after clicking login

Look for elements in the snapshot with [ref=eN] markers.
Type credentials one field at a time."""

        conversation = []

        for step in range(self._max_login_steps):
            conversation.append({"role": "user", "content": prompt})
            response = await self.llm.complete(prompt, BROWSER_ACTION_PROMPT, conversation[:-1], json_mode=True)
            conversation.append({"role": "assistant", "content": response.content})

            # Log full LLM response
            logger.info(f"LLM login step {step + 1} response:\n{response.content}")

            decision = self.llm.parse_json(response.content)
            action = decision.get("action", "")
            args = decision.get("args", {})
            thought = decision.get("thought", "")

            logger.info(f"Login step {step + 1}: action={action}, thought={thought}")

            if action == "done":
                logger.info("LLM indicated login complete")

                # Check if LLM result indicates failure
                result_text = decision.get("result", "").lower()
                if any(err in result_text for err in ["fail", "incorrect", "wrong", "error", "неверн"]):
                    logger.error(f"LLM reported login failure: {decision.get('result')}")
                    return False

                # Wait for page to load after login
                await self.call_tool("browser_wait_for", {"time": 3})

                # Verify we're logged in
                snapshot = await self.get_snapshot()
                current_url = self._parse_url(snapshot)
                logger.info(f"After login, URL: {current_url}")
                logger.info(f"After login snapshot:\n{snapshot[:1500]}")

                # Check if still on login page
                snapshot_lower = snapshot.lower()
                if "login" in current_url.lower() or "password" in snapshot_lower[:800] or "пароль" in snapshot_lower[:800]:
                    # Check for error indicators
                    error_patterns = ["неверн", "invalid", "incorrect", "ошибка", "error", "failed", "wrong"]
                    if any(err in snapshot_lower for err in error_patterns):
                        logger.error("Login error detected on page")
                        return False

                    # Still on login page but no error - login likely failed
                    logger.error("Still on login page after login attempt - credentials may be incorrect")
                    return False

                # Extract cookies after successful login
                self._cookies = await self.get_cookies()
                self._authenticated = True
                logger.info(f"Login successful, cookies: {self._cookies[:100] if self._cookies else 'none'}...")
                return True

            if action == "error":
                logger.error(f"LLM reported error: {thought}")
                return False

            # Execute browser action
            if action in ["browser_click", "browser_type", "browser_snapshot",
                         "browser_wait_for", "browser_navigate", "browser_press_key"]:
                try:
                    result = await self.call_tool(action, args)
                    logger.info(f"Tool result: {result[:500] if result else 'empty'}")
                    prompt = f"Action result:\n{result}\n\nContinue with the login process. What's next?"
                except Exception as e:
                    logger.warning(f"Tool error: {e}")
                    prompt = f"Action failed with error: {e}\n\nTry a different approach."
            else:
                prompt = f"Unknown action '{action}'. Use browser_type, browser_click, browser_press_key, or done."

        logger.error("Max login steps reached")
        return False

    def _is_login_page_from_snapshot(self, url: str, snapshot: str) -> bool:
        """Check if current page is login page based on Playwright snapshot."""
        url_lower = url.lower()

        # URL patterns indicating login
        if any(p in url_lower for p in ["/login", "/signin", "/auth", "login?", "required=true"]):
            return True

        # Content patterns - look for password field and login text
        snapshot_lower = snapshot.lower()
        has_password = "password" in snapshot_lower or "пароль" in snapshot_lower
        has_login_text = any(w in snapshot_lower for w in ["login", "sign in", "войти", "вход"])

        return has_password and has_login_text

    async def extract_video(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
    ) -> VideoInfo:
        """Extract video URL and cookies from page."""
        logger.info(f"Extracting video: {url}")

        # Navigate to page
        await self.call_tool("browser_navigate", {"url": url})
        await self.call_tool("browser_wait_for", {"time": 3})

        # Get snapshot from Playwright (rendered page with JS)
        snapshot = await self.get_snapshot()
        current_url = self._parse_url(snapshot) or url
        logger.info(f"Current URL: {current_url}")
        logger.info(f"Page snapshot:\n{snapshot}")

        # Check if we need to login using the rendered snapshot (not httpx)
        if not self._authenticated and self._is_login_page_from_snapshot(current_url, snapshot):
            logger.info("Login page detected from Playwright snapshot")

            if not username or not password:
                return VideoInfo(page_url=url, error="Login required but no credentials provided")

            # Step 1: Also check with httpx to get redirect URL for reference
            redirect_url, _ = detect_login_redirect(url)
            logger.info(f"Redirect URL from httpx: {redirect_url}")

            # Create LoginInfo from snapshot analysis
            login_info = LoginInfo(
                is_login_page=True,
                redirect_url=redirect_url or current_url,
                reason="Login page detected from Playwright snapshot (has password field and login text)"
            )

            # Step 4-5: Perform login using Playwright snapshot
            if not await self.perform_login_with_llm(username, password, login_info):
                return VideoInfo(page_url=url, error="Login failed")

            # Step 7: Navigate back to original URL after login
            logger.info(f"Navigating to original URL: {url}")
            await self.call_tool("browser_navigate", {"url": url})
            await self.call_tool("browser_wait_for", {"time": 3})
            snapshot = await self.get_snapshot()

        # Extract cookies if not already done
        if not self._cookies:
            self._cookies = await self.get_cookies()

        # Check network for video URLs
        network = await self.get_network_requests()
        video_url = self._find_video_in_network(network)

        if video_url:
            return VideoInfo(
                page_url=url,
                video_url=video_url,
                title=self._parse_title(snapshot),
                cookies=self._cookies,
            )

        # Try to find video with LLM
        return await self._extract_video_with_llm(url, snapshot)

    async def _extract_video_with_llm(self, url: str, snapshot: str) -> VideoInfo:
        """Use LLM to find video URL on page."""
        logger.info("Using LLM to find video URL...")

        prompt = f"""Find the video URL on this page.

Page URL: {url}

Page snapshot:
{snapshot}

Look for video elements, download buttons, or player controls.
Click play buttons if needed to load the video.
Return "done" with the video URL when found, or "error" if no video found."""

        conversation = []

        for step in range(self._max_extract_steps):
            conversation.append({"role": "user", "content": prompt})
            response = await self.llm.complete(prompt, VIDEO_EXTRACTION_PROMPT, conversation[:-1], json_mode=True)
            conversation.append({"role": "assistant", "content": response.content})

            # Log full response
            logger.info(f"LLM video extraction step {step + 1}:\n{response.content}")

            decision = self.llm.parse_json(response.content)
            action = decision.get("action", "")
            args = decision.get("args", {})
            result_text = decision.get("result", "")
            thought = decision.get("thought", "")

            if action == "done":
                video_url = result_text.strip()
                if not video_url:
                    match = re.search(r'https?://[^\s<>"\']+\.(?:mp4|m3u8|webm|mpd)[^\s<>"\']*', thought + result_text)
                    if match:
                        video_url = match.group(0)

                if video_url and video_url.startswith("http"):
                    return VideoInfo(
                        page_url=url,
                        video_url=video_url,
                        cookies=self._cookies,
                    )
                else:
                    return VideoInfo(page_url=url, error=f"No valid URL found: {thought}")

            if action == "error":
                return VideoInfo(page_url=url, error=thought or "Video extraction failed")

            if action in ["browser_click", "browser_snapshot", "browser_wait_for", "browser_navigate"]:
                try:
                    result = await self.call_tool(action, args)
                    logger.info(f"Tool result: {result[:500] if result else 'empty'}")

                    # Check network after each action
                    network = await self.get_network_requests()
                    video_url = self._find_video_in_network(network)
                    if video_url:
                        return VideoInfo(
                            page_url=url,
                            video_url=video_url,
                            cookies=self._cookies,
                        )

                    prompt = f"Action result:\n{result}\n\nContinue searching for video URL."
                except Exception as e:
                    prompt = f"Error: {e}. Try different approach."
            else:
                prompt = f"Unknown action '{action}'. Use browser_click, browser_snapshot, done, or error."

        return VideoInfo(page_url=url, error="Max steps reached without finding video")

    async def extract_videos(
        self,
        urls: list[str],
        username: str | None = None,
        password: str | None = None,
    ) -> list[VideoInfo]:
        """
        Extract videos from multiple URLs.
        Session and cookies persist between URLs - login once, process all.
        """
        results = []

        for i, url in enumerate(urls):
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing {i + 1}/{len(urls)}: {url}")
            logger.info(f"{'='*60}")

            info = await self.extract_video(url, username, password)
            results.append(info)

            if info.video_url:
                logger.info(f"SUCCESS: {info.video_url[:80]}...")
            else:
                logger.warning(f"FAILED: {info.error}")

        return results
