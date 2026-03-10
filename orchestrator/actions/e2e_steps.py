"""Orchestrator step implementations for the Phase 2.5 E2E lab workflow.

Each ``step_*`` function is self-contained: it takes structured config,
calls worker HTTP endpoints in parallel where applicable, saves per-step
artifacts, and returns a result dict with ``success`` and details.

All step functions accept an optional *artifacts_dir* parameter so the
sweep runner (Phase 3) can redirect output to per-iteration directories.
When ``None``, the module-level ``ARTIFACTS_DIR`` is used.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from orchestrator.logging.json_logger import get_logger
from orchestrator.utils.retry import retry_async
from orchestrator.actions.router_netgear import apply_router_settings
from orchestrator.workflow_schema import (
    BandWifiConfig,
    RouterConfig,
    WorkerTarget,
    ScanConfig,
    PingGateConfig,
    AutomationConfig,
)
from router.netgear_nighthawk.selectors import BandConfig

logger = get_logger("e2e_steps")

ARTIFACTS_DIR = os.path.join(os.path.abspath("."), "artifacts")
HTTP_TIMEOUT = 120.0
RETRY_MAX = 3
RETRY_BACKOFF = 3.0


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _resolve_artifacts(artifacts_dir: Optional[str] = None) -> str:
    d = artifacts_dir or ARTIFACTS_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _save_artifact(
    filename: str,
    data: Any,
    artifacts_dir: Optional[str] = None,
) -> str:
    """Write *data* as JSON and return the path."""
    d = _resolve_artifacts(artifacts_dir)
    path = os.path.join(d, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return path


def _worker_label(w: WorkerTarget) -> str:
    return w.name or w.url


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------

async def _call_worker(
    method: str,
    worker: WorkerTarget,
    path: str,
    payload: Optional[dict] = None,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, Any]:
    """HTTP call to a single worker with retry."""
    url = f"{worker.url.rstrip('/')}{path}"

    async def _do():
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                resp = await client.get(url, params=payload)
            else:
                resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    try:
        return await retry_async(_do, max_retries=RETRY_MAX, backoff=RETRY_BACKOFF, timeout=timeout + 30)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Step 1: Router apply
# ---------------------------------------------------------------------------

async def step_router_apply(
    router_cfg: RouterConfig,
    router_user: str,
    router_pass: str,
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Configure router via Playwright (runs locally on the orchestrator PC)."""
    adir = _resolve_artifacts(artifacts_dir)

    band_configs: dict[str, BandConfig] = {}
    for band_key, bwc in router_cfg.bands.items():
        band_configs[band_key] = BandConfig(
            ssid=bwc.ssid,
            password=bwc.password,
            channel=bwc.channel,
            security=bwc.security,
        )

    result = await apply_router_settings(
        base_url=router_cfg.base_url,
        user=router_user,
        password=router_pass,
        band_configs=band_configs,
        artifacts_dir=adir,
    )

    _save_artifact(f"step_router_apply_{_ts()}.json", result, adir)
    return result


# ---------------------------------------------------------------------------
# Step 2: Wait for SSID broadcast (scan)
# ---------------------------------------------------------------------------

async def step_wait_ssid_broadcast(
    workers: list[WorkerTarget],
    scan_cfg: ScanConfig,
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Poll ``GET /wifi/scan`` on all workers until every one sees *target_ssid*."""
    adir = _resolve_artifacts(artifacts_dir)
    target = scan_cfg.target_ssid.lower()
    deadline = time.monotonic() + scan_cfg.timeout_sec
    interval = scan_cfg.poll_interval_sec
    per_worker: dict[str, dict] = {_worker_label(w): {"found": False} for w in workers}

    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        pending = [w for w in workers if not per_worker[_worker_label(w)]["found"]]
        if not pending:
            break

        logger.info(
            "SSID scan attempt %d – %d workers pending", attempt, len(pending),
            extra={"action": "wait_ssid", "step": "poll"},
        )

        tasks = [_call_worker("GET", w, "/wifi/scan") for w in pending]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for w, res in zip(pending, results):
            label = _worker_label(w)
            if isinstance(res, Exception):
                per_worker[label]["last_error"] = str(res)
                continue
            per_worker[label]["last_scan"] = res
            networks = (res.get("verification") or {}).get("networks", [])
            for net in networks:
                if (net.get("ssid") or "").lower() == target:
                    per_worker[label]["found"] = True
                    break

        if all(pw["found"] for pw in per_worker.values()):
            break
        await asyncio.sleep(interval)

    success = all(pw["found"] for pw in per_worker.values())
    result = {"success": success, "target_ssid": scan_cfg.target_ssid, "workers": per_worker}
    _save_artifact(f"step_wait_ssid_{_ts()}.json", result, adir)

    if not success:
        missing = [k for k, v in per_worker.items() if not v["found"]]
        logger.error(
            "SSID %s not seen by: %s", scan_cfg.target_ssid, missing,
            extra={"action": "wait_ssid", "step": "timeout"},
        )
    else:
        logger.info(
            "All workers see SSID %s", scan_cfg.target_ssid,
            extra={"action": "wait_ssid", "step": "done"},
        )
    return result


# ---------------------------------------------------------------------------
# Step 3: Connect workers to Wi-Fi
# ---------------------------------------------------------------------------

async def step_connect_workers(
    workers: list[WorkerTarget],
    ssid: str,
    password: str,
    interface: Optional[str] = None,
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """POST /wifi/connect on all workers in parallel."""
    adir = _resolve_artifacts(artifacts_dir)
    payload: dict[str, Any] = {"ssid": ssid, "password": password}
    if interface:
        payload["interface"] = interface

    tasks = [_call_worker("POST", w, "/wifi/connect", payload) for w in workers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    per_worker: dict[str, dict] = {}
    all_ok = True
    for w, res in zip(workers, results):
        label = _worker_label(w)
        if isinstance(res, Exception):
            per_worker[label] = {"success": False, "error": str(res)}
            all_ok = False
        else:
            per_worker[label] = res
            if not res.get("success", False):
                all_ok = False

    result = {"success": all_ok, "workers": per_worker}
    _save_artifact(f"step_connect_workers_{_ts()}.json", result, adir)

    if all_ok:
        logger.info("All workers connected", extra={"action": "connect_workers", "step": "done"})
    else:
        failed = [k for k, v in per_worker.items() if not v.get("success")]
        logger.error(
            "Workers failed to connect: %s", failed,
            extra={"action": "connect_workers", "step": "fail"},
        )
    return result


# ---------------------------------------------------------------------------
# Step 4: Ping gate
# ---------------------------------------------------------------------------

async def step_ping_gate(
    workers: list[WorkerTarget],
    ping_cfg: PingGateConfig,
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """POST /net/ping on all workers. Gate passes only if ALL succeed."""
    adir = _resolve_artifacts(artifacts_dir)
    payload = {
        "host": ping_cfg.host,
        "count": ping_cfg.count,
        "timeout_sec": ping_cfg.timeout_sec,
    }

    tasks = [_call_worker("POST", w, "/net/ping", payload) for w in workers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    per_worker: dict[str, dict] = {}
    all_ok = True
    for w, res in zip(workers, results):
        label = _worker_label(w)
        if isinstance(res, Exception):
            per_worker[label] = {"success": False, "error": str(res)}
            all_ok = False
        else:
            per_worker[label] = res
            if not res.get("success", False):
                all_ok = False

    result = {
        "success": all_ok,
        "gate_host": ping_cfg.host,
        "workers": per_worker,
    }
    _save_artifact(f"step_ping_gate_{_ts()}.json", result, adir)

    if all_ok:
        logger.info(
            "Ping gate PASSED (all workers reached %s)", ping_cfg.host,
            extra={"action": "ping_gate", "step": "pass"},
        )
    else:
        failed = [k for k, v in per_worker.items() if not v.get("success")]
        logger.error(
            "Ping gate FAILED – workers unable to reach %s: %s", ping_cfg.host, failed,
            extra={"action": "ping_gate", "step": "fail"},
        )
    return result


# ---------------------------------------------------------------------------
# Step 5: Run automation
# ---------------------------------------------------------------------------

async def step_run_automation(
    workers: list[WorkerTarget],
    auto_cfg: AutomationConfig,
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """POST /automation/run on target workers, then poll until all complete."""
    adir = _resolve_artifacts(artifacts_dir)

    target_urls = set(auto_cfg.target_workers) if auto_cfg.target_workers else None
    targets = [w for w in workers if target_urls is None or w.url in target_urls]

    payload = {
        "command": auto_cfg.command,
        "args": auto_cfg.args,
        "cwd": auto_cfg.cwd,
        "timeout_sec": auto_cfg.timeout_sec,
    }

    launch_tasks = [_call_worker("POST", w, "/automation/run", payload) for w in targets]
    launch_results = await asyncio.gather(*launch_tasks, return_exceptions=True)

    job_map: dict[str, dict] = {}
    per_worker: dict[str, dict] = {}

    for w, res in zip(targets, launch_results):
        label = _worker_label(w)
        if isinstance(res, Exception):
            per_worker[label] = {"success": False, "error": str(res)}
            continue
        job_id = res.get("job_id")
        if not job_id:
            per_worker[label] = {"success": False, "error": "No job_id returned", "raw": res}
            continue
        job_map[label] = {"worker": w, "job_id": job_id}
        per_worker[label] = {"job_id": job_id, "status": "running"}

    poll_timeout = auto_cfg.timeout_sec + 60
    poll_deadline = time.monotonic() + poll_timeout
    poll_interval = 3.0

    while time.monotonic() < poll_deadline:
        pending = {lbl: info for lbl, info in job_map.items()
                   if per_worker[lbl].get("status") == "running"}
        if not pending:
            break

        poll_tasks = [
            _call_worker("GET", info["worker"], "/automation/status",
                         {"job_id": info["job_id"]})
            for info in pending.values()
        ]
        poll_results = await asyncio.gather(*poll_tasks, return_exceptions=True)

        for (lbl, info), res in zip(pending.items(), poll_results):
            if isinstance(res, Exception):
                continue
            status = res.get("status", "running")
            if status in ("completed", "failed"):
                per_worker[lbl] = res

        await asyncio.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 15)

    for lbl in job_map:
        if per_worker[lbl].get("status") == "running":
            per_worker[lbl]["status"] = "timeout"
            per_worker[lbl]["success"] = False

    all_ok = all(
        pw.get("status") == "completed" and pw.get("exit_code", -1) == 0
        for pw in per_worker.values()
    )

    result = {"success": all_ok, "workers": per_worker}
    _save_artifact(f"step_automation_{_ts()}.json", result, adir)

    if all_ok:
        logger.info("All automation jobs completed successfully",
                     extra={"action": "run_automation", "step": "done"})
    else:
        failed = [k for k, v in per_worker.items()
                  if v.get("status") != "completed" or v.get("exit_code", -1) != 0]
        logger.error("Automation failed on: %s", failed,
                      extra={"action": "run_automation", "step": "fail"})
    return result


# ---------------------------------------------------------------------------
# Step 5b: Automation noop (placeholder when automation is disabled)
# ---------------------------------------------------------------------------

async def step_run_automation_noop(
    workers: list[WorkerTarget],
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Return a ``status: skipped`` result with the expected field structure.

    This keeps the per-iteration ``final_report.json`` schema identical
    regardless of whether real automation is enabled.
    """
    adir = _resolve_artifacts(artifacts_dir)
    per_worker: dict[str, dict] = {}
    for w in workers:
        per_worker[_worker_label(w)] = {
            "status": "skipped",
            "exit_code": None,
            "log_path": None,
            "elapsed_sec": None,
            "error": None,
        }

    result = {"success": True, "workers": per_worker}
    _save_artifact(f"step_automation_noop_{_ts()}.json", result, adir)
    logger.info("Automation step skipped (noop)", extra={"action": "run_automation", "step": "noop"})
    return result


# ---------------------------------------------------------------------------
# Final report builder
# ---------------------------------------------------------------------------

def build_final_report(
    workflow_name: str,
    workers: list[WorkerTarget],
    step_results: list[dict[str, Any]],
    artifacts_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble ``final_report.json`` from collected step results."""
    adir = _resolve_artifacts(artifacts_dir)

    overall_success = all(r.get("success", False) for r in step_results)

    worker_summary: dict[str, dict] = {}
    for w in workers:
        label = _worker_label(w)
        worker_summary[label] = {}

    for sr in step_results:
        action = sr.get("action", "")
        workers_data = sr.get("workers", {})
        for label, data in workers_data.items():
            if label not in worker_summary:
                worker_summary[label] = {}
            if action == "wait_ssid_broadcast":
                worker_summary[label]["scan_ssid_found"] = data.get("found", False)
            elif action == "wifi_connect_workers":
                worker_summary[label]["connect"] = data
            elif action == "ping_gate":
                worker_summary[label]["ping"] = {
                    k: data.get(k)
                    for k in ("success", "loss_percent", "avg_latency_ms", "host", "error")
                    if data.get(k) is not None
                }
            elif action == "run_automation":
                worker_summary[label]["automation"] = {
                    k: data.get(k)
                    for k in ("status", "exit_code", "log_path", "elapsed_sec", "error")
                    if data.get(k) is not None
                }

    failed_step = None
    for i, sr in enumerate(step_results):
        if not sr.get("success", False):
            failed_step = {
                "step_index": i,
                "action": sr.get("action", ""),
                "error": sr.get("error"),
            }
            break

    artifacts_list: list[str] = []
    try:
        for fname in os.listdir(adir):
            artifacts_list.append(os.path.join(adir, fname))
    except FileNotFoundError:
        pass

    router_result = None
    for sr in step_results:
        if sr.get("action") == "router_apply":
            router_result = {
                k: sr.get(k)
                for k in ("detected_bands", "configured_bands", "error")
                if sr.get(k) is not None
            }
            break

    report = {
        "workflow": workflow_name,
        "success": overall_success,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "router_apply": router_result,
        "workers": worker_summary,
        "failed_step": failed_step,
        "steps": step_results,
        "artifacts": sorted(artifacts_list),
    }

    path = _save_artifact("final_report.json", report, adir)
    logger.info("Final report written to %s", path, extra={"action": "report", "step": "done"})
    return report
