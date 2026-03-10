import socket
import subprocess
from typing import Optional

import httpx

from orchestrator.logging.json_logger import get_logger

logger = get_logger("net")


def ping(host: str, timeout: int = 5) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout * 1000), host],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        success = result.returncode == 0
        logger.info(
            "Ping %s: %s",
            host,
            "OK" if success else "FAIL",
            extra={"action": "ping", "step": "result"},
        )
        return success
    except Exception as exc:
        logger.error("Ping %s error: %s", host, exc, extra={"action": "ping", "step": "error"})
        return False


def dns_resolve(hostname: str) -> Optional[str]:
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        if results:
            addr = results[0][4][0]
            logger.info(
                "DNS resolve %s -> %s", hostname, addr,
                extra={"action": "dns_resolve", "step": "result"},
            )
            return addr
    except Exception as exc:
        logger.error(
            "DNS resolve %s failed: %s", hostname, exc,
            extra={"action": "dns_resolve", "step": "error"},
        )
    return None


async def http_reachable(url: str, timeout: float = 10.0) -> bool:
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
            reachable = resp.status_code < 500
            logger.info(
                "HTTP check %s: status=%d reachable=%s",
                url, resp.status_code, reachable,
                extra={"action": "http_reachable", "step": "result"},
            )
            return reachable
    except Exception as exc:
        logger.warning(
            "HTTP check %s unreachable: %s", url, exc,
            extra={"action": "http_reachable", "step": "error"},
        )
        return False
