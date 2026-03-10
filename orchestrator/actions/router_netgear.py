from __future__ import annotations

from typing import Any

from orchestrator.logging.json_logger import get_logger
from router.netgear_nighthawk.driver import NetgearNighthawkDriver
from router.netgear_nighthawk.selectors import BandConfig

logger = get_logger("router_netgear")


async def apply_router_settings(
    base_url: str,
    user: str,
    password: str,
    band_configs: dict[str, BandConfig],
    artifacts_dir: str = "artifacts",
) -> dict[str, Any]:
    """Login, auto-detect bands, configure per-band SSID/channel, apply.

    *band_configs* maps band keys (``"2.4G"``, ``"5G"``, ``"6G"``) to
    their desired configuration.  Bands present in the config but missing
    from the router hardware are silently skipped.
    """
    driver = NetgearNighthawkDriver(base_url=base_url, artifacts_dir=artifacts_dir)
    try:
        await driver.open()
        await driver.login(user, password)
        await driver.navigate_to_wireless()

        detected = await driver.detect_available_bands()
        active_configs = {b: c for b, c in band_configs.items() if b in detected}
        skipped = [b for b in band_configs if b not in detected]
        if skipped:
            logger.info(
                "Bands not present on this router, skipping: %s", skipped,
                extra={"action": "apply_router", "step": "bands_skipped"},
            )

        logger.info(
            "Configuring bands: %s",
            {b: c.ssid for b, c in active_configs.items()},
            extra={"action": "apply_router", "step": "configure"},
        )

        await driver.set_wireless(active_configs)
        await driver.apply()
        await driver.wait_until_ready()

        logger.info(
            "Router settings applied successfully",
            extra={"action": "apply_router", "step": "done"},
        )
        return {
            "success": True,
            "step": "apply_router",
            "detected_bands": detected,
            "configured_bands": list(active_configs.keys()),
            "error": None,
        }

    except Exception as exc:
        logger.error(
            "Router settings failed: %s", exc,
            extra={"action": "apply_router", "step": "error"},
        )
        return {"success": False, "step": "apply_router", "error": str(exc)}
    finally:
        await driver.close()
