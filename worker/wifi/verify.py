import subprocess
import time
from typing import Any, Optional

from worker.logging.json_logger import get_logger
from worker.utils.timeouts import VERIFY_TIMEOUT, POLL_INTERVAL
from worker.wifi.netsh import get_connected_ssid, get_ipv4_and_gateway

logger = get_logger("wifi_verify")


def ping_host(host: str, timeout: int = 5) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout * 1000), host],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return result.returncode == 0
    except Exception:
        return False


def dns_resolve(hostname: str = "www.msftconnecttest.com") -> Optional[str]:
    import socket
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        return results[0][4][0] if results else None
    except Exception:
        return None


def verify_connection(
    target_ssid: str,
    interface: Optional[str] = None,
    timeout: float = VERIFY_TIMEOUT,
) -> dict[str, Any]:
    start = time.monotonic()
    interval = POLL_INTERVAL
    last_state: dict[str, Any] = {}

    while time.monotonic() - start < timeout:
        connected_ssid = get_connected_ssid(interface)
        ssid_match = connected_ssid is not None and connected_ssid.lower() == target_ssid.lower()

        net_info = get_ipv4_and_gateway(interface)
        ipv4 = net_info["ipv4"]
        gateway = net_info["gateway"]

        ping_ok = False
        if gateway:
            ping_ok = ping_host(gateway)

        dns_ok = False
        dns_result = dns_resolve()
        if dns_result:
            dns_ok = True

        last_state = {
            "ssid_match": ssid_match,
            "connected_ssid": connected_ssid,
            "target_ssid": target_ssid,
            "ipv4": ipv4,
            "gateway": gateway,
            "ping_ok": ping_ok,
            "dns_ok": dns_ok,
            "elapsed": round(time.monotonic() - start, 2),
        }

        logger.info(
            "Verify: ssid_match=%s ipv4=%s ping=%s dns=%s",
            ssid_match, ipv4, ping_ok, dns_ok,
            extra={"action": "verify_connection", "step": "poll"},
        )

        if ssid_match and ipv4 and ping_ok:
            last_state["success"] = True
            return last_state

        time.sleep(interval)
        interval = min(interval * 1.5, 10)

    last_state["success"] = False
    last_state["error"] = "Verification timed out"
    logger.error(
        "Verification timed out for %s", target_ssid,
        extra={"action": "verify_connection", "step": "timeout"},
    )
    return last_state
