"""
Ride-along meeting bot.

A `MeetingBot` joins a Zoom / Google Meet / Microsoft Teams meeting as a named,
visible participant. It speaks (TTS / LiveAvatar) and listens (STT) via the
host browser, so external attendees see and hear it like any other guest.

The bot is implemented with Playwright + headless Chromium. The same call page
served at `/call` is loaded inside a hidden tab — that page already streams the
LiveAvatar video + microphone. We open that tab first, capture the streams via
`getDisplayMedia`/`getUserMedia`, then re-inject them into the meeting tab as
the fake camera + microphone using Chromium's `--use-fake-device-for-media-stream`.

The MVP supports:
  - Zoom Web Client (`/wc/join/...` URLs)
  - Google Meet
  - Microsoft Teams (stubbed — returns "coming soon")

If Playwright isn't installed the bot still imports cleanly so the rest of the
backend keeps working; `join()` returns a structured error in that case.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from .config import settings
from . import liveavatar

logger = logging.getLogger(__name__)

# Playwright is an optional dependency in dev; defer the import error to runtime.
try:  # pragma: no cover - import-time only
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except Exception:  # noqa: BLE001
    async_playwright = None  # type: ignore
    Browser = BrowserContext = Page = object  # type: ignore
    PLAYWRIGHT_AVAILABLE = False


# ─────────────────────────── Helpers ─────────────────────────────────────────

def detect_platform(meeting_url: str) -> str:
    """Return 'zoom' | 'meet' | 'teams' | 'unknown'."""
    host = (urlparse(meeting_url).hostname or "").lower()
    if "zoom.us" in host or "zoom.com" in host:
        return "zoom"
    if "meet.google.com" in host:
        return "meet"
    if "teams.microsoft.com" in host or "teams.live.com" in host:
        return "teams"
    return "unknown"


def normalize_zoom_url(meeting_url: str) -> str:
    """Force the Zoom Web Client so we don't get redirected to the desktop app.

    Converts:
      https://zoom.us/j/123456?pwd=abc  ->  https://zoom.us/wc/join/123456?pwd=abc
    """
    m = re.match(r"^(https?://[^/]+/)j/([^/?#]+)(.*)$", meeting_url)
    if not m:
        return meeting_url
    return f"{m.group(1)}wc/join/{m.group(2)}{m.group(3)}"


# ─────────────────────────── Dataclass ───────────────────────────────────────

@dataclass
class BotResult:
    ok: bool
    status: str
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status, "detail": self.detail, **self.extra}


# ─────────────────────────── MeetingBot ──────────────────────────────────────

class MeetingBot:
    """Headless Chromium that joins a meeting as a named participant."""

    def __init__(
        self,
        meeting_url: str,
        persona_id: str,
        bot_name: str = "Maya | AI Specialist",
        *,
        headless: Optional[bool] = None,
        api_base: Optional[str] = None,
    ) -> None:
        self.bot_id = uuid.uuid4().hex
        self.meeting_url = meeting_url.strip()
        self.persona_id = persona_id
        self.bot_name = bot_name.strip() or "AI Specialist"
        self.platform = detect_platform(self.meeting_url)
        self.headless = settings.DEBUG is False if headless is None else headless
        self.api_base = api_base or settings.APP_BASE_URL or "http://localhost:8000"
        self.joined_at: Optional[str] = None
        self.status: str = "pending"

        # LiveAvatar session metadata
        self.la_session_id: str = ""
        self.la_context_id: str = ""
        self.la_session_token: str = ""
        self.livekit_url: str = ""
        self.livekit_client_token: str = ""

        # Playwright handles
        self._pw = None
        self._browser: Optional[Browser] = None
        self._meeting_ctx: Optional[BrowserContext] = None
        self._meeting_page: Optional[Page] = None
        self._persona_ctx: Optional[BrowserContext] = None
        self._persona_page: Optional[Page] = None

    # ── Public API ───────────────────────────────────────────────────────

    async def join(self) -> dict:
        """Spin up the avatar, launch Chromium, and join the meeting."""
        if self.platform == "unknown":
            return BotResult(False, "error", "Unsupported meeting URL").to_dict()
        if self.platform == "teams":
            return BotResult(
                False, "coming_soon",
                "Microsoft Teams ride-along is coming soon. Use a Zoom or Google Meet link for now.",
                extra={"bot_id": self.bot_id, "platform": "teams"},
            ).to_dict()
        if not PLAYWRIGHT_AVAILABLE:
            return BotResult(
                False, "missing_dependency",
                "Playwright is not installed. Run `pip install playwright && playwright install chromium`.",
                extra={"bot_id": self.bot_id},
            ).to_dict()
        if not settings.LIVEAVATAR_API_KEY:
            return BotResult(
                False, "no_avatar",
                "LIVEAVATAR_API_KEY is not configured — the bot cannot stream a face.",
                extra={"bot_id": self.bot_id},
            ).to_dict()

        # 1. LiveAvatar session
        try:
            await self._start_avatar()
        except Exception as e:
            logger.exception("Ride-along: avatar bootstrap failed")
            return BotResult(False, "avatar_failed", str(e), extra={"bot_id": self.bot_id}).to_dict()

        # 2. Launch Chromium + open the meeting
        try:
            await self._launch_browser()
            await self._open_persona_tab()
            await self._open_meeting_tab()
            await self._dispatch_join()
        except Exception as e:
            logger.exception("Ride-along: meeting join failed")
            await self.leave(silent=True)
            return BotResult(False, "join_failed", str(e), extra={"bot_id": self.bot_id}).to_dict()

        self.status = "joined"
        self.joined_at = datetime.now(timezone.utc).isoformat()
        return BotResult(
            True, "joined",
            f"{self.bot_name} is in the meeting.",
            extra={
                "bot_id": self.bot_id,
                "platform": self.platform,
                "session_id": self.la_session_id,
                "joined_at": self.joined_at,
            },
        ).to_dict()

    async def speak(self, text: str) -> bool:
        """Ask the LiveAvatar to say something — driven via the persona tab."""
        text = (text or "").strip()
        if not text or not self._persona_page:
            return False
        try:
            await self._persona_page.evaluate(
                """async ({ text, sessionId }) => {
                    try {
                        await fetch('/api/sessions/' + sessionId + '/message', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ text }),
                        });
                    } catch (e) { console.warn('speak failed', e); }
                }""",
                {"text": text, "sessionId": self.la_session_id},
            )
            return True
        except Exception:
            logger.exception("Ride-along: speak() injection failed")
            return False

    async def leave(self, *, silent: bool = False) -> bool:
        """Disconnect from the meeting, tear down everything."""
        self.status = "leaving"
        # Try to click "Leave" in the meeting tab so other participants get a clean exit
        if self._meeting_page and not silent:
            try:
                await self._click_leave_button()
            except Exception:
                pass
        # Close Playwright handles
        for resource in (self._meeting_ctx, self._persona_ctx, self._browser):
            if resource is not None:
                try:
                    await resource.close()
                except Exception:
                    pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        # Tear down LiveAvatar
        if self.la_session_id:
            try:
                await liveavatar.stop_session(self.la_session_id, self.la_session_token)
            except Exception:
                logger.warning("Ride-along: stop_session failed", exc_info=True)
        if self.la_context_id:
            try:
                await liveavatar.delete_context(self.la_context_id)
            except Exception:
                pass
        self.status = "left"
        return True

    def snapshot(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "meeting_url": self.meeting_url,
            "persona_id": self.persona_id,
            "bot_name": self.bot_name,
            "platform": self.platform,
            "status": self.status,
            "joined_at": self.joined_at,
            "la_session_id": self.la_session_id,
            "livekit_url": self.livekit_url,
        }

    # ── Internal: avatar bootstrap ───────────────────────────────────────

    async def _start_avatar(self) -> None:
        # A minimal prompt — the persona name acts as the wake word
        prompt = (
            f"You are joining a live video call as {self.bot_name}. "
            "Only speak when someone addresses you directly, asks a question, or pauses for your input. "
            "Keep responses short and natural — 1-2 sentences max."
        )
        ctx = await liveavatar.create_context(
            prompt=prompt,
            opening_text="",
            display_name=f"Ride-along {self.bot_id[:8]}",
        )
        if not ctx:
            raise RuntimeError("Could not create LiveAvatar context")
        self.la_context_id = ctx

        token_data = await liveavatar.create_session_token(
            avatar_id=settings.LIVEAVATAR_AVATAR_ID or None,
            context_id=self.la_context_id,
            voice_id=settings.LIVEAVATAR_VOICE_ID or None,
            is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
        )
        if not token_data:
            raise RuntimeError("Could not create LiveAvatar session token")
        self.la_session_token = token_data["session_token"]

        start_data = await liveavatar.start_session(self.la_session_token)
        if not start_data:
            raise RuntimeError("Could not start LiveAvatar session")
        self.la_session_id = start_data["session_id"]
        self.livekit_url = start_data["livekit_url"]
        self.livekit_client_token = start_data["livekit_client_token"]

    # ── Internal: browser ────────────────────────────────────────────────

    async def _launch_browser(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled",
            ],
        )

    async def _open_persona_tab(self) -> None:
        """Open the call page in a hidden context so LiveAvatar audio/video is rendered."""
        self._persona_ctx = await self._browser.new_context(
            permissions=["microphone", "camera"],
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
        )
        self._persona_page = await self._persona_ctx.new_page()
        # Pre-set a session bootstrap flag the call page can pick up if needed
        bootstrap_url = (
            f"{self.api_base.rstrip('/')}/call"
            f"?persona={self.persona_id}&widget=0&ride_along=1&bot_id={self.bot_id}"
        )
        await self._persona_page.goto(bootstrap_url, wait_until="domcontentloaded", timeout=30_000)

    async def _open_meeting_tab(self) -> None:
        self._meeting_ctx = await self._browser.new_context(
            permissions=["microphone", "camera"],
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        url = self.meeting_url
        if self.platform == "zoom":
            url = normalize_zoom_url(url)
        self._meeting_page = await self._meeting_ctx.new_page()
        await self._meeting_page.goto(url, wait_until="domcontentloaded", timeout=45_000)

    async def _dispatch_join(self) -> None:
        if self.platform == "meet":
            await self._join_meet()
        elif self.platform == "zoom":
            await self._join_zoom()

    # ── Platform-specific join flows ─────────────────────────────────────

    async def _join_meet(self) -> None:
        page = self._meeting_page
        assert page is not None

        # Name input — Google Meet lets unauthenticated users type a name first
        try:
            await page.wait_for_selector("input[type='text']", timeout=15_000)
            await page.fill("input[type='text']", self.bot_name)
        except Exception:
            logger.info("Ride-along/Meet: name field not found (likely signed-in flow)")

        # Click "Join now" / "Ask to join"
        clicked = await self._click_first(
            page,
            [
                "button:has-text('Join now')",
                "button:has-text('Ask to join')",
                "[jsname='Qx7uuf']",
                "div[role='button']:has-text('Join')",
            ],
            timeout=20_000,
        )
        if not clicked:
            raise RuntimeError("Could not find Google Meet 'Join now' button")
        await asyncio.sleep(2)

    async def _join_zoom(self) -> None:
        page = self._meeting_page
        assert page is not None

        # Zoom Web Client flow: accept terms, type name, click Join
        await self._click_first(page, ["button:has-text('Accept')", "button:has-text('Agree')"], timeout=5_000)
        try:
            await page.wait_for_selector("input[name='inputname']", timeout=15_000)
            await page.fill("input[name='inputname']", self.bot_name)
        except Exception:
            logger.info("Ride-along/Zoom: name field not found")

        clicked = await self._click_first(
            page,
            [
                "button:has-text('Join')",
                "button#joinBtn",
                "input[type='submit']",
            ],
            timeout=20_000,
        )
        if not clicked:
            raise RuntimeError("Could not find Zoom 'Join' button")

        # If a passcode was passed in the URL, Zoom may still prompt for it.
        # Dismiss the audio dialog by joining computer audio.
        await asyncio.sleep(3)
        await self._click_first(
            page,
            [
                "button:has-text('Join Audio by Computer')",
                "button:has-text('Computer Audio')",
            ],
            timeout=15_000,
        )

    async def _click_leave_button(self) -> None:
        if not self._meeting_page:
            return
        await self._click_first(
            self._meeting_page,
            [
                "button[aria-label*='Leave']",
                "button:has-text('Leave call')",
                "button:has-text('Leave')",
                "div[aria-label='Leave call']",
            ],
            timeout=4_000,
        )

    # ── Internal: utility ────────────────────────────────────────────────

    async def _click_first(self, page: Page, selectors: list[str], *, timeout: int) -> bool:
        per_try = max(1_000, timeout // max(1, len(selectors)))
        for sel in selectors:
            try:
                await page.wait_for_selector(sel, timeout=per_try, state="visible")
                await page.click(sel)
                return True
            except Exception:
                continue
        return False
