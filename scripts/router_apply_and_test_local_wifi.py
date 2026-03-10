"""Phase 2 + local Wi-Fi test (per-band SSID/channel support):
1) Router driver apply -- set per-band SSID/password/channel via Playwright
2) wait_until_ready()
3) Local Wi-Fi connect + verify (netsh / WLAN API) against chosen band
4) Output result JSON to artifacts/result.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from orchestrator.logging.json_logger import get_logger
from orchestrator.actions.router_netgear import apply_router_settings
from orchestrator.actions.wifi_local import connect_local
from router.netgear_nighthawk.selectors import BandConfig

logger = get_logger("router_apply_and_test")


async def main(
    band_configs: dict[str, BandConfig],
    base_url: str,
    connect_band: str,
):
    load_dotenv()
    router_user = os.environ.get("ROUTER_USER", "admin")
    router_pass = os.environ.get("ROUTER_PASS", "")
    artifacts_dir = os.path.abspath("artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)

    overall_start = time.monotonic()
    result: dict = {
        "band_configs": {b: {"ssid": c.ssid, "channel": c.channel} for b, c in band_configs.items()},
        "base_url": base_url,
        "connect_band": connect_band,
        "phases": {},
        "success": False,
    }

    logger.info(
        "Phase 2: Apply router settings (bands=%s, base_url=%s)",
        list(band_configs.keys()), base_url,
        extra={"action": "phase2", "step": "router_apply_start"},
    )
    router_result = await apply_router_settings(
        base_url=base_url,
        user=router_user,
        password=router_pass,
        band_configs=band_configs,
        artifacts_dir=artifacts_dir,
    )
    result["phases"]["router_apply"] = router_result

    if not router_result.get("success", False):
        logger.error(
            "Router apply failed, aborting",
            extra={"action": "phase2", "step": "router_apply_fail"},
        )
        result["success"] = False
        result["error"] = "Router apply failed"
        _write_result(result, artifacts_dir)
        return result

    connect_cfg = band_configs.get(connect_band)
    if connect_cfg is None:
        logger.error(
            "Connect band %s not in band_configs", connect_band,
            extra={"action": "phase2", "step": "bad_connect_band"},
        )
        result["success"] = False
        result["error"] = f"Connect band {connect_band} not configured"
        _write_result(result, artifacts_dir)
        return result

    logger.info(
        "Phase 2: Local Wi-Fi connect (ssid=%s, band=%s)", connect_cfg.ssid, connect_band,
        extra={"action": "phase2", "step": "local_wifi_start"},
    )
    wifi_result = connect_local(connect_cfg.ssid, connect_cfg.password)
    result["phases"]["local_wifi"] = wifi_result

    result["success"] = wifi_result.get("success", False)
    result["total_elapsed"] = round(time.monotonic() - overall_start, 2)

    if result["success"]:
        logger.info("Phase 2 PASSED", extra={"action": "phase2", "step": "done"})
    else:
        logger.error(
            "Phase 2 FAILED: %s", wifi_result.get("error"),
            extra={"action": "phase2", "step": "fail"},
        )

    _write_result(result, artifacts_dir)
    return result


def _write_result(result: dict, artifacts_dir: str):
    out_path = os.path.join(artifacts_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info("Result written to %s", out_path, extra={"action": "output", "step": "write"})
    print(json.dumps(result, indent=2, default=str))


def _build_band_configs(args) -> dict[str, BandConfig]:
    configs: dict[str, BandConfig] = {}
    if args.ssid_2g:
        configs["2.4G"] = BandConfig(ssid=args.ssid_2g, password=args.password, channel=args.ch_2g)
    if args.ssid_5g:
        configs["5G"] = BandConfig(ssid=args.ssid_5g, password=args.password, channel=args.ch_5g)
    if args.ssid_6g:
        configs["6G"] = BandConfig(ssid=args.ssid_6g, password=args.password, channel=args.ch_6g)
    return configs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase 2: Router apply (per-band SSID/channel) + local Wi-Fi test",
    )
    parser.add_argument("--ssid-2g", default="RFLabTest_2G", help="2.4 GHz SSID (default: RFLabTest_2G)")
    parser.add_argument("--ssid-5g", default="RFLabTest_5G", help="5 GHz SSID (default: RFLabTest_5G)")
    parser.add_argument("--ssid-6g", default="RFLabTest_6G", help="6 GHz SSID (default: RFLabTest_6G)")
    parser.add_argument("--ch-2g", default="10", help="2.4 GHz channel (default: 10)")
    parser.add_argument("--ch-5g", default="44", help="5 GHz channel (default: 44)")
    parser.add_argument("--ch-6g", default="69", help="6 GHz channel (default: 69)")
    parser.add_argument("--password", default="password", help="Wi-Fi password for all bands (default: password)")
    parser.add_argument("--base-url", default="http://192.168.1.1", help="Router base URL")
    parser.add_argument(
        "--connect-band", default="2.4G",
        help="Which band SSID to test local Wi-Fi connect against (default: 2.4G)",
    )
    args = parser.parse_args()

    band_configs = _build_band_configs(args)
    result = asyncio.run(main(band_configs, args.base_url, args.connect_band))
    sys.exit(0 if result.get("success") else 1)
