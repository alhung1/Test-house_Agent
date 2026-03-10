"""Phase 3 — Multi-Band Channel Sweep runner.

Reads a :class:`SweepWorkflow` YAML, auto-detects bands from the router GUI,
then iterates over every ``(band, channel)`` combination running the full E2E
pipeline per iteration.

Usage (via CLI wrapper)::

    python scripts/run_sweep_lab.py --workflow workflows/sweep_lab.yaml
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import yaml
from dotenv import load_dotenv

from orchestrator.logging.json_logger import get_logger
from orchestrator.workflow_schema import (
    SweepWorkflow,
    RouterConfig,
    BandWifiConfig,
    ScanConfig,
    PingGateConfig,
    AutomationConfig,
    WorkerTarget,
)
from orchestrator.actions.router_netgear import detect_router_bands
from orchestrator.actions.e2e_steps import (
    step_router_apply,
    step_wait_ssid_broadcast,
    step_connect_workers,
    step_ping_gate,
    step_run_automation,
    step_run_automation_noop,
    build_final_report,
    _save_artifact,
    _resolve_artifacts,
)

logger = get_logger("sweep_runner")

BAND_SSID_SUFFIX = {"2.4G": "_2G", "5G": "_5G", "6G": "_6G"}
SWEEP_ROOT = os.path.join(os.path.abspath("."), "artifacts", "sweeps")


def load_sweep_workflow(path: str) -> SweepWorkflow:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return SweepWorkflow(**data)


def _iteration_dir(band: str, channel: int | str) -> str:
    return os.path.join(SWEEP_ROOT, band, f"ch_{channel}")


def _build_router_cfg_for_iteration(
    base_url: str,
    detected_bands: list[str],
    base_ssid: str,
    password: str,
    test_band: str,
    test_channel: str,
) -> RouterConfig:
    """Build a RouterConfig that sets ALL detected bands' SSID/password but
    only changes the *test_band*'s channel."""
    bands: dict[str, BandWifiConfig] = {}
    for band in detected_bands:
        suffix = BAND_SSID_SUFFIX.get(band, f"_{band}")
        bands[band] = BandWifiConfig(
            ssid=f"{base_ssid}{suffix}",
            password=password,
            channel=str(test_channel) if band == test_band else None,
        )
    return RouterConfig(base_url=base_url, bands=bands)


async def run_sweep(sweep_wf: SweepWorkflow) -> dict[str, Any]:
    """Execute the full multi-band channel sweep and return the summary."""
    load_dotenv()
    router_user = os.environ.get("ROUTER_USER", "admin")
    router_pass = os.environ.get("ROUTER_PASS", "")
    sweep = sweep_wf.sweep
    workers = sweep_wf.workers
    base_url = sweep_wf.router.base_url

    overall_start = time.monotonic()
    os.makedirs(SWEEP_ROOT, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Detect available bands from router GUI
    # ------------------------------------------------------------------
    detect_artifacts = os.path.join(SWEEP_ROOT, "_detect_bands")
    os.makedirs(detect_artifacts, exist_ok=True)

    logger.info("Detecting available bands from router GUI",
                extra={"action": "sweep", "step": "detect_start"})
    try:
        detected_bands = await detect_router_bands(
            base_url=base_url,
            user=router_user,
            password=router_pass,
            artifacts_dir=detect_artifacts,
        )
    except Exception as exc:
        logger.error("Band detection failed: %s", exc,
                     extra={"action": "sweep", "step": "detect_fail"})
        summary = {
            "workflow": sweep_wf.name,
            "success": False,
            "error": f"Band detection failed: {exc}",
            "detected_bands": [],
            "total_iterations": 0,
            "passed": 0,
            "failed": 0,
            "iterations": [],
        }
        _save_artifact("sweep_summary.json", summary,
                        os.path.join(os.path.abspath("."), "artifacts"))
        return summary

    logger.info("Detected bands: %s", detected_bands,
                extra={"action": "sweep", "step": "detect_done"})

    # ------------------------------------------------------------------
    # 2. Build iteration list (filter by detected bands)
    # ------------------------------------------------------------------
    iterations: list[tuple[str, int]] = []
    for band in detected_bands:
        channels = sweep.channels.get(band, [])
        for ch in channels:
            iterations.append((band, ch))

    skipped_bands = [b for b in sweep.channels if b not in detected_bands]
    if skipped_bands:
        logger.info("Bands not detected, skipping channels for: %s", skipped_bands,
                     extra={"action": "sweep", "step": "bands_skipped"})

    logger.info("Sweep plan: %d iterations across %s",
                len(iterations), detected_bands,
                extra={"action": "sweep", "step": "plan"})

    # ------------------------------------------------------------------
    # 3. Execute iterations
    # ------------------------------------------------------------------
    iteration_results: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for idx, (band, channel) in enumerate(iterations):
        iter_label = f"{band}/ch_{channel}"
        iter_dir = _iteration_dir(band, channel)
        os.makedirs(iter_dir, exist_ok=True)

        logger.info(
            "=== Iteration %d/%d: %s ===", idx + 1, len(iterations), iter_label,
            extra={"action": "sweep_iter", "step": "start"},
        )

        iter_success = True
        step_results: list[dict[str, Any]] = []
        failed_step_name: str | None = None

        # --- router_apply ---
        router_cfg = _build_router_cfg_for_iteration(
            base_url=base_url,
            detected_bands=detected_bands,
            base_ssid=sweep.base_ssid,
            password=sweep.password,
            test_band=band,
            test_channel=str(channel),
        )
        result = await step_router_apply(
            router_cfg=router_cfg,
            router_user=router_user,
            router_pass=router_pass,
            artifacts_dir=iter_dir,
        )
        result["action"] = "router_apply"
        step_results.append(result)
        if not result.get("success"):
            iter_success = False
            failed_step_name = "router_apply"

        # --- wait_ssid_broadcast ---
        if iter_success:
            ssid_suffix = BAND_SSID_SUFFIX.get(band, f"_{band}")
            target_ssid = f"{sweep.base_ssid}{ssid_suffix}"
            scan_cfg = ScanConfig(
                target_ssid=target_ssid,
                timeout_sec=sweep.scan_timeout_sec,
                poll_interval_sec=sweep.scan_poll_interval_sec,
            )
            result = await step_wait_ssid_broadcast(
                workers=workers, scan_cfg=scan_cfg, artifacts_dir=iter_dir,
            )
            result["action"] = "wait_ssid_broadcast"
            step_results.append(result)
            if not result.get("success"):
                iter_success = False
                failed_step_name = "wait_ssid_broadcast"

        # --- wifi_connect_workers ---
        if iter_success:
            ssid_suffix = BAND_SSID_SUFFIX.get(band, f"_{band}")
            connect_ssid = f"{sweep.base_ssid}{ssid_suffix}"
            result = await step_connect_workers(
                workers=workers,
                ssid=connect_ssid,
                password=sweep.password,
                artifacts_dir=iter_dir,
            )
            result["action"] = "wifi_connect_workers"
            step_results.append(result)
            if not result.get("success"):
                iter_success = False
                failed_step_name = "wifi_connect_workers"

        # --- ping_gate ---
        if iter_success:
            ping_cfg = PingGateConfig(
                host=sweep.target_ping_ip,
                count=sweep.ping_count,
                timeout_sec=sweep.ping_timeout_sec,
            )
            result = await step_ping_gate(
                workers=workers, ping_cfg=ping_cfg, artifacts_dir=iter_dir,
            )
            result["action"] = "ping_gate"
            step_results.append(result)
            if not result.get("success"):
                iter_success = False
                failed_step_name = "ping_gate"

        # --- run_automation (noop or real) ---
        if iter_success and sweep.automation_enabled and sweep.automation:
            result = await step_run_automation(
                workers=workers, auto_cfg=sweep.automation, artifacts_dir=iter_dir,
            )
            result["action"] = "run_automation"
            step_results.append(result)
            if not result.get("success"):
                iter_success = False
                failed_step_name = "run_automation"
        else:
            result = await step_run_automation_noop(
                workers=workers, artifacts_dir=iter_dir,
            )
            result["action"] = "run_automation"
            step_results.append(result)

        # --- per-iteration final_report ---
        report = build_final_report(
            workflow_name=f"{sweep_wf.name} [{iter_label}]",
            workers=workers,
            step_results=step_results,
            artifacts_dir=iter_dir,
        )

        iter_entry = {
            "band": band,
            "channel": channel,
            "success": iter_success,
            "failed_step": failed_step_name,
            "report_path": os.path.join(iter_dir, "final_report.json"),
        }
        iteration_results.append(iter_entry)

        if iter_success:
            passed += 1
            logger.info("Iteration %s PASSED", iter_label,
                         extra={"action": "sweep_iter", "step": "pass"})
        else:
            failed += 1
            logger.error("Iteration %s FAILED at %s", iter_label, failed_step_name,
                          extra={"action": "sweep_iter", "step": "fail"})
            if not sweep.continue_on_failure:
                logger.info("Stopping sweep (continue_on_failure=false)",
                             extra={"action": "sweep", "step": "abort"})
                break

    # ------------------------------------------------------------------
    # 4. Sweep summary
    # ------------------------------------------------------------------
    elapsed = round(time.monotonic() - overall_start, 2)
    summary = {
        "workflow": sweep_wf.name,
        "success": failed == 0 and passed == len(iterations),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": elapsed,
        "detected_bands": detected_bands,
        "skipped_bands": skipped_bands,
        "total_iterations": len(iterations),
        "completed": passed + failed,
        "passed": passed,
        "failed": failed,
        "iterations": iteration_results,
    }

    summary_path = _save_artifact(
        "sweep_summary.json", summary,
        os.path.join(os.path.abspath("."), "artifacts"),
    )
    logger.info(
        "Sweep complete: %d/%d passed (%.1fs) – %s",
        passed, len(iterations), elapsed, summary_path,
        extra={"action": "sweep", "step": "done"},
    )
    return summary
