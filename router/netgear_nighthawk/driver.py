"""Playwright driver for Netgear Nighthawk router.

Architecture of the Nighthawk web UI:
  1. Login page at /  (flat HTML, no frames)
  2. After login -> index.htm with frameset:
       topframe  -> top.html  (navigation + menu)
       formframe -> basic_home.htm  (content area)
  3. Wireless settings: formframe navigates to WLG_wireless.htm
  4. Form submits via POST to /apply.cgi with onclick=check_wlan()
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Frame

from orchestrator.logging.json_logger import get_logger
from orchestrator.utils.retry import retry_async
from orchestrator.utils.timeouts import (
    ROUTER_APPLY_TIMEOUT,
    ROUTER_LOGIN_TIMEOUT,
    ROUTER_NAVIGATE_TIMEOUT,
    POLL_INTERVAL,
    POLL_BACKOFF,
)
from router.netgear_nighthawk.selectors import (
    BandConfig,
    LOGIN_USERNAME,
    LOGIN_PASSWORD,
    LOGIN_BUTTON,
    WIRELESS_PAGE,
    APPLY_BUTTON,
    SMART_CONNECT_CHECKBOX,
    BAND_SELECTORS,
    SECURITY_VALUES,
)
from router.netgear_nighthawk.evidence import collect_evidence

logger = get_logger("netgear_driver")


class NetgearNighthawkDriver:
    def __init__(self, base_url: str = "http://192.168.1.1", artifacts_dir: str = "artifacts"):
        self.base_url = base_url.rstrip("/")
        self.artifacts_dir = os.path.abspath(artifacts_dir)
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._formframe: Optional[Frame] = None

    async def open(self) -> None:
        logger.info("Opening browser for %s", self.base_url, extra={"action": "open", "step": "start"})
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            ignore_https_errors=True,
            record_har_path=os.path.join(self.artifacts_dir, "network.har"),
        )
        await self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(ROUTER_LOGIN_TIMEOUT * 1000)

        async def _navigate():
            await self._page.goto(self.base_url, wait_until="domcontentloaded", timeout=ROUTER_LOGIN_TIMEOUT * 1000)

        await retry_async(_navigate, max_retries=3, backoff=2.0, timeout=60.0)
        logger.info("Router login page loaded", extra={"action": "open", "step": "done"})

    async def login(self, user: str, password: str) -> None:
        logger.info("Logging in as %s", user, extra={"action": "login", "step": "start"})
        page = self._page
        try:
            await page.wait_for_selector(LOGIN_USERNAME, timeout=10000)
            await page.fill(LOGIN_USERNAME, user)
            await page.fill(LOGIN_PASSWORD, password)

            login_btn = page.locator(LOGIN_BUTTON)
            await login_btn.click()

            try:
                await page.wait_for_url("**/index.htm*", timeout=15000)
            except Exception:
                await page.wait_for_url("**/start.htm*", timeout=10000)

            await page.wait_for_timeout(2000)

            formframe = page.frame("formframe")
            if formframe is None:
                raise RuntimeError(
                    f"formframe not found after login. URL={page.url}, "
                    f"frames={[f.name for f in page.frames]}"
                )
            self._formframe = formframe
            logger.info(
                "Login successful (formframe=%s)", formframe.url,
                extra={"action": "login", "step": "done"},
            )
        except Exception as exc:
            logger.error("Login failed: %s", exc, extra={"action": "login", "step": "error"})
            await collect_evidence(page, self._context, self.artifacts_dir, "login_fail")
            raise

    async def navigate_to_wireless(self) -> None:
        logger.info("Navigating to wireless settings", extra={"action": "navigate", "step": "start"})
        frame = self._formframe
        try:
            await frame.goto(
                f"{self.base_url}{WIRELESS_PAGE}",
                wait_until="domcontentloaded",
                timeout=ROUTER_NAVIGATE_TIMEOUT * 1000,
            )
            await frame.wait_for_load_state("networkidle", timeout=ROUTER_NAVIGATE_TIMEOUT * 1000)
            await self._page.wait_for_timeout(2000)

            ssid_field = frame.locator("input[name='ssid']")
            if await ssid_field.count() == 0:
                raise RuntimeError(
                    f"Wireless form not found. Frame URL={frame.url}. "
                    "Possibly redirected back to login."
                )

            current_ssid = await ssid_field.get_attribute("value") or ""
            logger.info(
                "Wireless page loaded (current SSID=%s)", current_ssid,
                extra={"action": "navigate", "step": "done"},
            )
        except Exception as exc:
            logger.error("Navigation failed: %s", exc, extra={"action": "navigate", "step": "error"})
            await collect_evidence(self._page, self._context, self.artifacts_dir, "navigate_fail")
            raise

    async def detect_available_bands(self) -> list[str]:
        """Probe the wireless page to discover which bands are present.

        Returns a list of band keys (e.g. ``["2.4G", "5G", "6G"]``)
        whose SSID input field exists and is visible.
        """
        frame = self._formframe
        detected: list[str] = []
        for band, sel in BAND_SELECTORS.items():
            loc = frame.locator(sel.ssid)
            try:
                if await loc.count() > 0 and await loc.is_visible():
                    detected.append(band)
            except Exception:
                pass
        logger.info(
            "Detected bands: %s", detected,
            extra={"action": "detect_bands", "step": "done"},
        )
        return detected

    async def _disable_smart_connect(self) -> None:
        """Uncheck Smart Connect if enabled, so per-band fields become editable."""
        frame = self._formframe
        sc = frame.locator(SMART_CONNECT_CHECKBOX)
        if await sc.count() > 0 and await sc.is_checked():
            logger.info("Disabling Smart Connect", extra={"action": "set_wireless", "step": "smart_connect_off"})
            await sc.uncheck()
            await self._page.wait_for_timeout(2000)

    async def _select_channel(self, frame: Frame, selector: str, channel: str) -> None:
        """Select a channel handling suffixes like ``(PSC)`` or ``(DFS)``."""
        ch_select = frame.locator(selector)
        if await ch_select.count() == 0:
            return

        # 1. Try exact value match
        try:
            await ch_select.select_option(value=channel, timeout=2000)
            return
        except Exception:
            pass

        # 2. Try exact label match
        try:
            await ch_select.select_option(label=channel, timeout=2000)
            return
        except Exception:
            pass

        # 3. Partial label match -- handles "69(PSC)", "52(DFS)", etc.
        options = ch_select.locator("option")
        count = await options.count()
        for i in range(count):
            opt = options.nth(i)
            label = (await opt.inner_text()).strip()
            if label.startswith(channel) and (
                len(label) == len(channel) or not label[len(channel)].isdigit()
            ):
                val = await opt.get_attribute("value") or label
                await ch_select.select_option(value=val)
                logger.info(
                    "Channel %s matched option '%s'", channel, label,
                    extra={"action": "set_wireless", "step": "channel_match"},
                )
                return

        logger.warning(
            "Channel %s not found in selector %s", channel, selector,
            extra={"action": "set_wireless", "step": "channel_miss"},
        )

    async def set_wireless(self, band_configs: dict[str, BandConfig]) -> None:
        """Configure each band with its own SSID, password, channel.

        Only bands whose SSID field is visible on the page are configured;
        missing bands are silently skipped so the same config dict works
        across 2-band and 3-band routers.
        """
        frame = self._formframe
        try:
            await self._disable_smart_connect()

            for band, cfg in band_configs.items():
                sel = BAND_SELECTORS.get(band)
                if sel is None:
                    logger.warning("Unknown band %s, skipping", band, extra={"action": "set_wireless"})
                    continue

                logger.info("Configuring %s band", band, extra={"action": "set_wireless", "step": f"{band}_start"})

                ssid_input = frame.locator(sel.ssid)
                if await ssid_input.count() == 0 or not await ssid_input.is_visible():
                    logger.warning(
                        "%s SSID field not visible, skipping", band,
                        extra={"action": "set_wireless", "step": f"{band}_skip"},
                    )
                    continue

                await ssid_input.fill(cfg.ssid)

                sec_value = SECURITY_VALUES.get(cfg.security, "WPA2-PSK")
                sec_radio = frame.locator(f"input[name='{sel.security_radio_name}'][value='{sec_value}']")
                if await sec_radio.count() > 0:
                    if not await sec_radio.is_checked():
                        await sec_radio.check(force=True)
                        await self._page.wait_for_timeout(500)

                passphrase_input = frame.locator(sel.passphrase)
                if await passphrase_input.count() > 0 and await passphrase_input.is_visible():
                    await passphrase_input.fill(cfg.password)

                if cfg.channel:
                    await self._select_channel(frame, sel.channel, cfg.channel)

                logger.info(
                    "%s configured (ssid=%s, ch=%s)", band, cfg.ssid, cfg.channel,
                    extra={"action": "set_wireless", "step": f"{band}_done"},
                )

            logger.info("Wireless settings configured", extra={"action": "set_wireless", "step": "done"})
        except Exception as exc:
            logger.error("Set wireless failed: %s", exc, extra={"action": "set_wireless", "step": "error"})
            await collect_evidence(self._page, self._context, self.artifacts_dir, "set_wireless_fail")
            raise

    async def apply(self) -> None:
        logger.info("Applying settings", extra={"action": "apply", "step": "start"})
        frame = self._formframe
        try:
            apply_btn = frame.locator(APPLY_BUTTON)
            if await apply_btn.count() == 0:
                raise RuntimeError("Apply button not found in formframe")

            await apply_btn.click()

            await self._page.wait_for_timeout(5000)
            logger.info("Apply clicked", extra={"action": "apply", "step": "clicked"})
        except Exception as exc:
            logger.error("Apply failed: %s", exc, extra={"action": "apply", "step": "error"})
            await collect_evidence(self._page, self._context, self.artifacts_dir, "apply_fail")
            raise

    async def wait_until_ready(self, timeout: float = ROUTER_APPLY_TIMEOUT) -> None:
        logger.info(
            "Waiting for router to become ready (timeout=%ss)", timeout,
            extra={"action": "wait_ready", "step": "start"},
        )
        interval = POLL_INTERVAL
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            try:
                async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                    resp = await client.get(self.base_url, follow_redirects=True)
                    if resp.status_code < 500:
                        logger.info(
                            "Router reachable (status=%d, elapsed=%.1fs)",
                            resp.status_code, elapsed,
                            extra={"action": "wait_ready", "step": "reachable"},
                        )
                        return
            except Exception:
                logger.info(
                    "Router not ready yet (elapsed=%.1fs)", elapsed,
                    extra={"action": "wait_ready", "step": "polling"},
                )
            interval = min(interval * POLL_BACKOFF, 15)
        raise TimeoutError(f"Router not reachable after {timeout}s")

    async def close(self) -> None:
        logger.info("Closing browser", extra={"action": "close", "step": "start"})
        try:
            if self._context:
                try:
                    await self._context.tracing.stop(path=os.path.join(self.artifacts_dir, "trace.zip"))
                except Exception:
                    pass
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.error("Close error: %s", exc, extra={"action": "close", "step": "error"})
        logger.info("Browser closed", extra={"action": "close", "step": "done"})
