from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class WifiConfig(BaseModel):
    """Legacy single-band Wi-Fi config (kept for backward compatibility)."""
    ssid: str
    password: str
    interface: Optional[str] = None


class BandWifiConfig(BaseModel):
    """Per-band wireless configuration used in the new router schema.

    ``ssid`` and ``password`` default to empty strings so the legacy
    ``bands: ["2.4G", "5G"]`` list format can be coerced without errors.
    When empty, the orchestrator should fall back to the workflow-level
    ``wifi.ssid`` / ``wifi.password``.
    """
    ssid: str = ""
    password: str = ""
    channel: Optional[str] = None
    security: str = "wpa2"


class RouterConfig(BaseModel):
    base_url: str = "http://192.168.1.1"
    bands: dict[str, BandWifiConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_bands(cls, values: Any) -> Any:
        """Accept the old ``bands: ["2.4G", "5G"]`` list format.

        When a list of strings is provided, each entry becomes a key with
        an empty BandWifiConfig (ssid/password must come from the legacy
        ``wifi:`` section at the workflow level).
        """
        if isinstance(values, dict):
            raw = values.get("bands")
            if isinstance(raw, list) and raw and isinstance(raw[0], str):
                values["bands"] = {b: {} for b in raw}
        return values


class WorkerTarget(BaseModel):
    url: str
    name: Optional[str] = None


class ScanConfig(BaseModel):
    target_ssid: str
    timeout_sec: int = 120
    poll_interval_sec: int = 10


class PingGateConfig(BaseModel):
    host: str = "192.168.1.100"
    count: int = 4
    timeout_sec: int = 5


class AutomationConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    timeout_sec: int = 300
    target_workers: Optional[list[str]] = None


class Step(BaseModel):
    action: str
    description: Optional[str] = None
    # Legacy fields (Phase 1 / Phase 2)
    wifi: Optional[WifiConfig] = None
    router: Optional[RouterConfig] = None
    workers: Optional[list[WorkerTarget]] = None
    wait_seconds: Optional[float] = None
    # Phase 2.5 fields
    scan: Optional[ScanConfig] = None
    ping_gate: Optional[PingGateConfig] = None
    automation: Optional[AutomationConfig] = None
    connect_band: Optional[str] = None


class Workflow(BaseModel):
    name: str
    description: Optional[str] = None
    wifi: Optional[WifiConfig] = None
    router: Optional[RouterConfig] = None
    workers: Optional[list[WorkerTarget]] = None
    steps: list[Step]


# ---------------------------------------------------------------------------
# Phase 3: Sweep workflow models
# ---------------------------------------------------------------------------

class SweepConfig(BaseModel):
    """Configuration for a multi-band channel sweep."""
    base_ssid: str = "RFLabTest"
    password: str = "password"
    target_ping_ip: str = "192.168.1.100"
    channels: dict[str, list[int]]
    continue_on_failure: bool = False
    scan_timeout_sec: int = 120
    scan_poll_interval_sec: int = 10
    ping_count: int = 4
    ping_timeout_sec: int = 5
    automation_enabled: bool = False
    automation: Optional[AutomationConfig] = None


class SweepWorkflow(BaseModel):
    """Top-level model for ``workflows/sweep_lab.yaml``."""
    name: str
    description: Optional[str] = None
    router: RouterConfig
    workers: list[WorkerTarget]
    sweep: SweepConfig
