import time
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from worker.logging.json_logger import get_logger
from worker.utils.retry import retry_sync
from worker.wifi import netsh, verify

logger = get_logger("worker_api")
app = FastAPI(title="Wi-Fi Worker Agent", version="1.0.0")


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
