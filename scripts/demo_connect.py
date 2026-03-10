"""Phase 1 demo: connect multiple remote workers to a Wi-Fi SSID in parallel."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.actions.wifi_remote import connect_multiple
from orchestrator.logging.json_logger import get_logger

logger = get_logger("demo_connect")


async def main(workers: list[str], ssid: str, password: str):
    logger.info(
        "Starting parallel connect: workers=%s ssid=%s", workers, ssid,
        extra={"action": "demo", "step": "start"},
    )
    report = await connect_multiple(workers, ssid, password)

    os.makedirs("artifacts", exist_ok=True)
    out_path = os.path.join("artifacts", "demo_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(json.dumps(report, indent=2, default=str))

    all_ok = all(
        r.get("success", False) if isinstance(r, dict) else False
        for r in report.values()
    )
    logger.info("Demo result: %s", "ALL OK" if all_ok else "SOME FAILED",
                extra={"action": "demo", "step": "done"})
    return 0 if all_ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo: connect workers to Wi-Fi")
    parser.add_argument(
        "--workers",
        required=True,
        help="Comma-separated worker URLs (e.g. http://host1:8080,http://host2:8080)",
    )
    parser.add_argument("--ssid", required=True, help="Target SSID")
    parser.add_argument("--password", required=True, help="Wi-Fi password")
    args = parser.parse_args()

    worker_list = [w.strip() for w in args.workers.split(",") if w.strip()]
    rc = asyncio.run(main(worker_list, args.ssid, args.password))
    sys.exit(rc)
