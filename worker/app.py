import os
import subprocess
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from worker.logging.json_logger import get_logger
from worker.utils.retry import retry_sync
from worker.wifi import netsh, verify
from worker.net.ping import run_ping

logger = get_logger("worker_api")
app = FastAPI(title="Wi-Fi Worker Agent", version="2.0.0")

ARTIFACTS_DIR = os.path.join(os.path.abspath("."), "artifacts")

# ---------------------------------------------------------------------------
# In-memory job store for automation runs
# ---------------------------------------------------------------------------
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    ssid: str
    password: str
    interface: Optional[str] = None


class WifiResponse(BaseModel):
    success: bool
    step: str
    stdout: str = ""
    stderr: str = ""
    error_code: int = 0
    timings: dict = Field(default_factory=dict)
    verification: Optional[dict] = None
    error: Optional[str] = None


class PingRequest(BaseModel):
    host: str = "192.168.1.100"
    count: int = 4
    timeout_sec: int = 5


class PingResponse(BaseModel):
    success: bool
    host: str
    packets_sent: int
    packets_received: int
    loss_percent: float
    avg_latency_ms: Optional[float] = None
    raw_output: str
    artifact_path: Optional[str] = None
    error: Optional[str] = None


class AutomationRunRequest(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    timeout_sec: int = 300


class AutomationRunResponse(BaseModel):
    job_id: str
    status: str


class AutomationStatusResponse(BaseModel):
    job_id: str
    status: str
    exit_code: Optional[int] = None
    log_path: Optional[str] = None
    elapsed_sec: Optional[float] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Wi-Fi endpoints (existing)
# ---------------------------------------------------------------------------

@app.post("/wifi/connect", response_model=WifiResponse)
def wifi_connect(req: ConnectRequest):
    timings: dict = {}
    t0 = time.monotonic()

    logger.info(
        "Connect request: ssid=%s", req.ssid,
        extra={"action": "wifi_connect", "step": "start"},
    )

    try:
        t_profile = time.monotonic()
        profile_result = retry_sync(
            netsh.add_profile,
            req.ssid,
            req.password,
            interface=req.interface,
            max_retries=2,
            backoff=1.0,
            timeout=15.0,
        )
        timings["add_profile"] = round(time.monotonic() - t_profile, 3)
        if not profile_result["success"]:
            return WifiResponse(
                success=False,
                step="add_profile",
                stdout=profile_result["stdout"],
                stderr=profile_result["stderr"],
                error_code=profile_result["return_code"],
                timings=timings,
                error="Failed to add Wi-Fi profile",
            )

        t_connect = time.monotonic()
        connect_result = retry_sync(
            netsh.connect,
            req.ssid,
            interface=req.interface,
            max_retries=3,
            backoff=2.0,
            timeout=30.0,
        )
        timings["connect"] = round(time.monotonic() - t_connect, 3)
        if not connect_result["success"]:
            return WifiResponse(
                success=False,
                step="connect",
                stdout=connect_result["stdout"],
                stderr=connect_result["stderr"],
                error_code=connect_result["return_code"],
                timings=timings,
                error="Failed to connect to Wi-Fi",
            )

        t_verify = time.monotonic()
        verification = verify.verify_connection(req.ssid, interface=req.interface)
        timings["verify"] = round(time.monotonic() - t_verify, 3)
        timings["total"] = round(time.monotonic() - t0, 3)

        return WifiResponse(
            success=verification.get("success", False),
            step="verify",
            stdout=connect_result["stdout"],
            stderr=connect_result["stderr"],
            error_code=0 if verification.get("success") else 1,
            timings=timings,
            verification=verification,
            error=verification.get("error"),
        )

    except Exception as exc:
        timings["total"] = round(time.monotonic() - t0, 3)
        logger.error(
            "Connect failed: %s", exc,
            extra={"action": "wifi_connect", "step": "exception"},
        )
        return WifiResponse(
            success=False,
            step="exception",
            error_code=-1,
            timings=timings,
            error=str(exc),
        )


@app.get("/wifi/status", response_model=WifiResponse)
def wifi_status():
    try:
        iface_result = netsh.get_interfaces()
        ssid = netsh.get_connected_ssid()
        net_info = netsh.get_ipv4_and_gateway()
        return WifiResponse(
            success=True,
            step="status",
            stdout=iface_result["stdout"],
            verification={
                "connected_ssid": ssid,
                "ipv4": net_info["ipv4"],
                "gateway": net_info["gateway"],
                "interfaces": iface_result.get("interfaces", []),
            },
        )
    except Exception as exc:
        return WifiResponse(success=False, step="status", error=str(exc), error_code=-1)


@app.get("/wifi/scan", response_model=WifiResponse)
def wifi_scan():
    try:
        scan_result = netsh.scan_networks()
        return WifiResponse(
            success=scan_result["success"],
            step="scan",
            stdout=scan_result["stdout"],
            stderr=scan_result["stderr"],
            error_code=scan_result["return_code"],
            verification={"networks": scan_result.get("networks", [])},
        )
    except Exception as exc:
        return WifiResponse(success=False, step="scan", error=str(exc), error_code=-1)


# ---------------------------------------------------------------------------
# Ping endpoint
# ---------------------------------------------------------------------------

@app.post("/net/ping", response_model=PingResponse)
def net_ping(req: PingRequest):
    logger.info(
        "Ping request: host=%s count=%d", req.host, req.count,
        extra={"action": "net_ping", "step": "start"},
    )
    result = run_ping(host=req.host, count=req.count, timeout_sec=req.timeout_sec)
    return PingResponse(**result)


# ---------------------------------------------------------------------------
# Automation endpoints
# ---------------------------------------------------------------------------

def _run_automation_job(job_id: str, command: str, args: list[str],
                        cwd: Optional[str], timeout_sec: int) -> None:
    """Background thread that runs a subprocess and updates the job store."""
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    log_path = os.path.join(ARTIFACTS_DIR, f"automation_{job_id}.log")

    with _jobs_lock:
        _jobs[job_id]["log_path"] = log_path
        _jobs[job_id]["status"] = "running"

    cmd = [command] + args
    logger.info(
        "Automation job %s starting: %s", job_id, " ".join(cmd),
        extra={"action": "automation_run", "step": "exec"},
    )

    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
            )
            try:
                exit_code = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                exit_code = -9
                log_file.write(f"\n[TIMEOUT] Process killed after {timeout_sec}s\n")

        status = "completed" if exit_code == 0 else "failed"
        with _jobs_lock:
            _jobs[job_id].update(
                status=status,
                exit_code=exit_code,
                end_time=time.monotonic(),
            )
        logger.info(
            "Automation job %s finished: exit_code=%d", job_id, exit_code,
            extra={"action": "automation_run", "step": "done"},
        )

    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id].update(
                status="failed",
                exit_code=-1,
                error=str(exc),
                end_time=time.monotonic(),
            )
        logger.error(
            "Automation job %s error: %s", job_id, exc,
            extra={"action": "automation_run", "step": "error"},
        )


@app.post("/automation/run", response_model=AutomationRunResponse)
def automation_run(req: AutomationRunRequest):
    job_id = str(uuid.uuid4())
    start_time = time.monotonic()

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "starting",
            "exit_code": None,
            "log_path": None,
            "start_time": start_time,
            "end_time": None,
            "error": None,
        }

    thread = threading.Thread(
        target=_run_automation_job,
        args=(job_id, req.command, req.args, req.cwd, req.timeout_sec),
        daemon=True,
    )
    thread.start()

    logger.info(
        "Automation job queued: job_id=%s command=%s", job_id, req.command,
        extra={"action": "automation_run", "step": "queued"},
    )
    return AutomationRunResponse(job_id=job_id, status="running")


@app.get("/automation/status", response_model=AutomationStatusResponse)
def automation_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)

    if job is None:
        return AutomationStatusResponse(job_id=job_id, status="not_found")

    elapsed = None
    if job["start_time"] is not None:
        end = job["end_time"] or time.monotonic()
        elapsed = round(end - job["start_time"], 2)

    return AutomationStatusResponse(
        job_id=job_id,
        status=job["status"],
        exit_code=job["exit_code"],
        log_path=job["log_path"],
        elapsed_sec=elapsed,
        error=job.get("error"),
    )
