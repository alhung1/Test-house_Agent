from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from orchestrator.logging.json_logger import get_logger
from orchestrator.utils.retry import retry_async
from orchestrator.utils.timeouts import HTTP_TIMEOUT

logger = get_logger("wifi_remote")


async def connect_remote(
    worker_url: str,
    ssid: str,
    password: str,
    interface: Optional[str] = None,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, Any]:
    url = f"{worker_url.rstrip('/')}/wifi/connect"
    payload = {"ssid": ssid, "password": password}
    if interface:
        payload["interface"] = interface

    logger.info(
        "Sending connect to %s (ssid=%s)", worker_url, ssid,
        extra={"action": "connect_remote", "step": "start"},
    )

    async def _post():
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    result = await retry_async(_post, max_retries=2, backoff=3.0, timeout=120.0)
    logger.info(
        "Worker %s response: success=%s", worker_url, result.get("success"),
        extra={"action": "connect_remote", "step": "done"},
    )
    return result


async def status_remote(worker_url: str) -> dict[str, Any]:
    url = f"{worker_url.rstrip('/')}/wifi/status"

    async def _get():
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    return await retry_async(_get, max_retries=2, backoff=2.0, timeout=30.0)


async def connect_multiple(
    workers: list[str],
    ssid: str,
    password: str,
    interface: Optional[str] = None,
) -> dict[str, Any]:
    tasks = [
        connect_remote(w, ssid, password, interface)
        for w in workers
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    report = {}
    for worker_url, result in zip(workers, results):
        if isinstance(result, Exception):
            report[worker_url] = {"success": False, "error": str(result)}
        else:
            report[worker_url] = result
    return report
