from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import Page, BrowserContext

from orchestrator.logging.json_logger import get_logger

logger = get_logger("evidence")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


async def save_screenshot(page: Page, artifacts_dir: str, prefix: str = "error") -> Optional[str]:
    path = os.path.join(artifacts_dir, f"screenshot_{prefix}_{_ts()}.png")
    try:
        await page.screenshot(path=path, full_page=True)
        logger.info("Screenshot saved: %s", path, extra={"action": "evidence", "step": "screenshot"})
        return path
    except Exception as exc:
        logger.error("Screenshot failed: %s", exc, extra={"action": "evidence", "step": "screenshot_error"})
        return None


async def save_html(page: Page, artifacts_dir: str, prefix: str = "error") -> Optional[str]:
    path = os.path.join(artifacts_dir, f"page_{prefix}_{_ts()}.html")
    try:
        content = await page.content()
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("HTML dump saved: %s", path, extra={"action": "evidence", "step": "html_dump"})
        return path
    except Exception as exc:
        logger.error("HTML dump failed: %s", exc, extra={"action": "evidence", "step": "html_error"})
        return None


async def stop_trace(context: BrowserContext, artifacts_dir: str) -> Optional[str]:
    path = os.path.join(artifacts_dir, f"trace_{_ts()}.zip")
    try:
        await context.tracing.stop(path=path)
        logger.info("Trace saved: %s", path, extra={"action": "evidence", "step": "trace"})
        return path
    except Exception as exc:
        logger.error("Trace save failed: %s", exc, extra={"action": "evidence", "step": "trace_error"})
        return None


async def collect_evidence(
    page: Optional[Page],
    context: Optional[BrowserContext],
    artifacts_dir: str,
    prefix: str = "error",
) -> dict[str, Optional[str]]:
    os.makedirs(artifacts_dir, exist_ok=True)
    result: dict[str, Optional[str]] = {
        "screenshot": None,
        "html": None,
        "trace": None,
    }
    if page:
        result["screenshot"] = await save_screenshot(page, artifacts_dir, prefix)
        result["html"] = await save_html(page, artifacts_dir, prefix)
    if context:
        result["trace"] = await stop_trace(context, artifacts_dir)
    logger.info("Evidence collected: %s", result, extra={"action": "evidence", "step": "complete"})
    return result
