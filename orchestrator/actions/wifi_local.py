from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Optional

from orchestrator.logging.json_logger import get_logger
from orchestrator.utils.timeouts import CONNECT_TIMEOUT, VERIFY_TIMEOUT, POLL_INTERVAL

logger = get_logger("wifi_local")

NETSH_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Low-level netsh wrapper
# ---------------------------------------------------------------------------

def _run_netsh(args: list[str], timeout: int = NETSH_TIMEOUT) -> dict[str, Any]:
    cmd = ["netsh"] + args
    logger.info("Running: %s", " ".join(cmd), extra={"action": "netsh", "step": "exec"})
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        if not stderr and result.returncode != 0:
            stderr = stdout
        return {
            "success": result.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timed out after {timeout}s", "return_code": -1}
    except Exception as exc:
        return {"success": False, "stdout": "", "stderr": str(exc), "return_code": -1}


def _run_powershell(cmd: str, timeout: int = NETSH_TIMEOUT) -> dict[str, Any]:
    """Run a PowerShell command and return structured result."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timed out after {timeout}s", "return_code": -1}
    except Exception as exc:
        return {"success": False, "stdout": "", "stderr": str(exc), "return_code": -1}


# ---------------------------------------------------------------------------
# Windows WLAN API via ctypes (bypasses netsh location-services requirement)
# ---------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.wintypes.DWORD),
        ("Data2", ctypes.wintypes.WORD),
        ("Data3", ctypes.wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", _GUID),
        ("strInterfaceDescription", ctypes.c_wchar * 256),
        ("isState", ctypes.c_uint),
    ]

class _WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", ctypes.wintypes.DWORD),
        ("dwIndex", ctypes.wintypes.DWORD),
        ("InterfaceInfo", _WLAN_INTERFACE_INFO * 1),
    ]

class _DOT11_SSID(ctypes.Structure):
    _fields_ = [
        ("uSSIDLength", ctypes.c_ulong),
        ("ucSSID", ctypes.c_ubyte * 32),
    ]

class _WLAN_CONNECTION_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("wlanConnectionMode", ctypes.c_uint),
        ("strProfile", ctypes.c_wchar_p),
        ("pDot11Ssid", ctypes.POINTER(_DOT11_SSID)),
        ("pDesiredBssidList", ctypes.c_void_p),
        ("dot11BssType", ctypes.c_uint),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def _wlan_api_connect(profile_name: str) -> dict[str, Any]:
    """Connect to a Wi-Fi profile using wlanapi.dll directly.

    This avoids the Windows Location Services requirement that blocks
    ``netsh wlan connect`` on newer Windows builds.
    """
    try:
        wlanapi = ctypes.windll.wlanapi
    except OSError as exc:
        return {"success": False, "error": f"wlanapi.dll not available: {exc}"}

    handle = ctypes.wintypes.HANDLE()
    neg_ver = ctypes.wintypes.DWORD()
    ret = wlanapi.WlanOpenHandle(2, None, ctypes.byref(neg_ver), ctypes.byref(handle))
    if ret != 0:
        return {"success": False, "error": f"WlanOpenHandle failed ({ret})"}

    iface_ptr = ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST)()
    ret = wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(iface_ptr))
    if ret != 0 or iface_ptr.contents.dwNumberOfItems == 0:
        wlanapi.WlanCloseHandle(handle, None)
        return {"success": False, "error": f"WlanEnumInterfaces failed ({ret})"}

    guid = iface_ptr.contents.InterfaceInfo[0].InterfaceGuid

    ssid = _DOT11_SSID()
    ssid_bytes = profile_name.encode("utf-8")
    ssid.uSSIDLength = len(ssid_bytes)
    for i, b in enumerate(ssid_bytes):
        ssid.ucSSID[i] = b

    params = _WLAN_CONNECTION_PARAMETERS()
    params.wlanConnectionMode = 0  # profile
    params.strProfile = profile_name
    params.pDot11Ssid = ctypes.pointer(ssid)
    params.pDesiredBssidList = None
    params.dot11BssType = 1  # infrastructure
    params.dwFlags = 0

    ret = wlanapi.WlanConnect(handle, ctypes.byref(guid), ctypes.byref(params), None)
    wlanapi.WlanFreeMemory(iface_ptr)
    wlanapi.WlanCloseHandle(handle, None)

    if ret == 0:
        return {"success": True, "error": None}
    return {"success": False, "error": f"WlanConnect failed ({ret})"}


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------

def create_profile_xml(ssid: str, password: str) -> str:
    hex_ssid = ssid.encode("utf-8").hex()
    return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID>
            <hex>{hex_ssid}</hex>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""


def add_profile(ssid: str, password: str, interface: Optional[str] = None) -> dict[str, Any]:
    xml_content = create_profile_xml(ssid, password)
    tmp_path = os.path.join(tempfile.gettempdir(), f"wlan_profile_{ssid}.xml")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        args = ["wlan", "add", "profile", f"filename={tmp_path}"]
        if interface:
            args += [f"interface={interface}"]
        return _run_netsh(args)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# Connect (try netsh first, fall back to WLAN API)
# ---------------------------------------------------------------------------

def connect_wifi(ssid: str, interface: Optional[str] = None) -> dict[str, Any]:
    args = ["wlan", "connect", f"name={ssid}"]
    if interface:
        args += [f"interface={interface}"]
    result = _run_netsh(args)

    if result["success"]:
        return result

    if "location" in result["stderr"].lower() or "error 5" in result["stderr"].lower():
        logger.info(
            "netsh wlan connect blocked by location services, falling back to WLAN API",
            extra={"action": "connect", "step": "wlan_api_fallback"},
        )
        api_result = _wlan_api_connect(ssid)
        return {
            "success": api_result["success"],
            "stdout": f"WLAN API: {'connected' if api_result['success'] else api_result.get('error', '')}",
            "stderr": "" if api_result["success"] else (api_result.get("error") or ""),
            "return_code": 0 if api_result["success"] else -1,
        }

    return result


# ---------------------------------------------------------------------------
# Status queries (PowerShell fallback when netsh wlan needs location services)
# ---------------------------------------------------------------------------

def get_connected_ssid(interface: Optional[str] = None) -> Optional[str]:
    """Get the SSID of the currently connected Wi-Fi network.

    Tries netsh first; if blocked by location services, uses
    PowerShell Get-NetConnectionProfile which does not require it.
    """
    result = _run_netsh(["wlan", "show", "interfaces"])
    if result["success"]:
        interfaces: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in result["stdout"].splitlines():
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().lower()
                val = val.strip()
                if key == "name":
                    if current:
                        interfaces.append(current)
                    current = {"name": val}
                elif current:
                    current[key] = val
            elif not line and current:
                interfaces.append(current)
                current = {}
        if current:
            interfaces.append(current)
        for iface in interfaces:
            if interface and iface.get("name") != interface:
                continue
            state = iface.get("state", "").lower()
            if state in ("connected", "已連線"):
                return iface.get("ssid", iface.get("profile"))
        return None

    logger.info(
        "netsh wlan show interfaces failed, using PowerShell fallback",
        extra={"action": "get_ssid", "step": "ps_fallback"},
    )
    alias = interface or "Wi-Fi"
    ps = _run_powershell(
        f"(Get-NetConnectionProfile -InterfaceAlias '{alias}' -ErrorAction SilentlyContinue).Name"
    )
    if ps["success"] and ps["stdout"]:
        return ps["stdout"].strip()
    return None


def get_ipv4_and_gateway(interface_name: Optional[str] = None) -> dict[str, Optional[str]]:
    iface = interface_name or "Wi-Fi"
    args = ["interface", "ip", "show", "address", f"name={iface}"]
    result = _run_netsh(args)
    ipv4 = None
    gateway = None
    if result["success"]:
        for line in result["stdout"].splitlines():
            ll = line.strip().lower()
            if "ip address" in ll or "ip 位址" in ll:
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    ipv4 = m.group(1)
            if "default gateway" in ll or "預設閘道" in ll:
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    gateway = m.group(1)
    return {"ipv4": ipv4, "gateway": gateway}


def ping_host(host: str, timeout: int = 5) -> bool:
    try:
        r = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout * 1000), host],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return r.returncode == 0
    except Exception:
        return False


def dns_resolve(hostname: str = "www.msftconnecttest.com") -> Optional[str]:
    import socket
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        return results[0][4][0] if results else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

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
        ping_ok = ping_host(gateway) if gateway else False
        dns_result = dns_resolve()
        dns_ok = dns_result is not None

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
            extra={"action": "verify_local", "step": "poll"},
        )

        if ssid_match and ipv4 and ping_ok:
            last_state["success"] = True
            return last_state

        time.sleep(interval)
        interval = min(interval * 1.5, 10)

    last_state["success"] = False
    last_state["error"] = "Verification timed out"
    return last_state


# ---------------------------------------------------------------------------
# Top-level connect + verify
# ---------------------------------------------------------------------------

def connect_local(
    ssid: str, password: str, interface: Optional[str] = None
) -> dict[str, Any]:
    timings: dict[str, float] = {}
    t0 = time.monotonic()

    logger.info("Local connect: ssid=%s", ssid, extra={"action": "connect_local", "step": "start"})

    t1 = time.monotonic()
    profile_result = add_profile(ssid, password, interface)
    timings["add_profile"] = round(time.monotonic() - t1, 3)
    if not profile_result["success"]:
        return {
            "success": False,
            "step": "add_profile",
            "error": profile_result["stderr"] or profile_result["stdout"],
            "timings": timings,
        }

    t2 = time.monotonic()
    connect_result = connect_wifi(ssid, interface)
    timings["connect"] = round(time.monotonic() - t2, 3)
    if not connect_result["success"]:
        return {
            "success": False,
            "step": "connect",
            "error": connect_result["stderr"] or connect_result["stdout"],
            "timings": timings,
        }

    time.sleep(5)

    t3 = time.monotonic()
    verification = verify_connection(ssid, interface)
    timings["verify"] = round(time.monotonic() - t3, 3)
    timings["total"] = round(time.monotonic() - t0, 3)

    return {
        "success": verification.get("success", False),
        "step": "verify",
        "verification": verification,
        "timings": timings,
        "error": verification.get("error"),
    }
