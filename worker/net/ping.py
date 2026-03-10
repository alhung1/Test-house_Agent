"""Windows ping wrapper with output parsing for English and Chinese locales."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

from worker.logging.json_logger import get_logger

logger = get_logger("net_ping")

ARTIFACTS_DIR = os.path.join(os.path.abspath("."), "artifacts")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_ping(
    host: str,
    count: int = 4,
    timeout_sec: int = 5,
) -> dict[str, Any]:
    """Run ``ping -n {count} -w {timeout_ms} {host}`` and parse the result.

    Returns a dict with ``success``, ``packets_sent``, ``packets_received``,
    ``loss_percent``, ``avg_latency_ms``, ``raw_output``, and ``artifact_path``.
    """
    timeout_ms = timeout_sec * 1000
    cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    logger.info("Running: %s", " ".join(cmd), extra={"action": "ping", "step": "exec"})

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=count * timeout_sec + 30,
        )
        raw = proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired:
        raw = f"ping subprocess timed out after {count * timeout_sec + 30}s"
        return _make_result(
            success=False, host=host,
            packets_sent=count, packets_received=0,
            loss_percent=100.0, avg_latency_ms=None,
            raw_output=raw, error="subprocess timeout",
        )
    except Exception as exc:
        return _make_result(
            success=False, host=host,
            packets_sent=count, packets_received=0,
            loss_percent=100.0, avg_latency_ms=None,
            raw_output=str(exc), error=str(exc),
        )

    sent, received, loss, avg = _parse_ping_output(raw, count)

    result = _make_result(
        success=received > 0 and loss < 100.0,
        host=host,
        packets_sent=sent,
        packets_received=received,
        loss_percent=loss,
        avg_latency_ms=avg,
        raw_output=raw,
    )

    artifact_path = _save_artifact(host, raw)
    if artifact_path:
        result["artifact_path"] = artifact_path

    logger.info(
        "Ping %s: sent=%d recv=%d loss=%.0f%% avg=%s",
        host, sent, received, loss, avg,
        extra={"action": "ping", "step": "done"},
    )
    return result


def _parse_ping_output(
    raw: str, expected_count: int
) -> tuple[int, int, float, Optional[float]]:
    """Parse Windows ping output (English + Chinese)."""
    sent = expected_count
    received = 0
    loss = 100.0
    avg: Optional[float] = None

    # English: Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)
    m = re.search(
        r"Sent\s*=\s*(\d+).*?Received\s*=\s*(\d+).*?Lost\s*=\s*(\d+).*?\((\d+)%",
        raw, re.IGNORECASE,
    )
    if not m:
        # Chinese: 已傳送 = 4, 已收到 = 4, 已遺失 = 0 (0% 遺失)
        m = re.search(
            r"傳送\s*=\s*(\d+).*?收到\s*=\s*(\d+).*?遺失\s*=\s*(\d+).*?\((\d+)%",
            raw,
        )
    if m:
        sent = int(m.group(1))
        received = int(m.group(2))
        loss = float(m.group(4))

    # English: Average = 2ms
    m_avg = re.search(r"Average\s*=\s*(\d+)\s*ms", raw, re.IGNORECASE)
    if not m_avg:
        # Chinese: 平均 = 2ms
        m_avg = re.search(r"平均\s*=\s*(\d+)\s*ms", raw)
    if m_avg:
        avg = float(m_avg.group(1))

    return sent, received, loss, avg


def _make_result(
    success: bool,
    host: str,
    packets_sent: int,
    packets_received: int,
    loss_percent: float,
    avg_latency_ms: Optional[float],
    raw_output: str,
    error: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "host": host,
        "packets_sent": packets_sent,
        "packets_received": packets_received,
        "loss_percent": loss_percent,
        "avg_latency_ms": avg_latency_ms,
        "raw_output": raw_output,
        "artifact_path": None,
        "error": error,
    }


def _save_artifact(host: str, raw_output: str) -> Optional[str]:
    try:
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        safe_host = host.replace(":", "_").replace("/", "_")
        path = os.path.join(ARTIFACTS_DIR, f"ping_{safe_host}_{_ts()}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw_output)
        return path
    except Exception as exc:
        logger.warning("Failed to save ping artifact: %s", exc)
        return None
