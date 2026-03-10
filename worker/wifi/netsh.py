import os
import subprocess
import tempfile
import re
from typing import Any, Optional

from worker.logging.json_logger import get_logger
from worker.utils.timeouts import NETSH_TIMEOUT

logger = get_logger("netsh")


def _run_netsh(args: list[str], timeout: int = NETSH_TIMEOUT) -> dict[str, Any]:
    cmd = ["netsh"] + args
    logger.info("Running: %s", " ".join(cmd), extra={"action": "netsh", "step": "exec"})
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
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
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "return_code": -1,
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(exc),
            "return_code": -1,
        }


def create_profile_xml(
    ssid: str,
    password: str,
    auth: str = "WPA2PSK",
    cipher: str = "AES",
) -> str:
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
                <authentication>{auth}</authentication>
                <encryption>{cipher}</encryption>
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


def add_profile(
    ssid: str, password: str, interface: Optional[str] = None
) -> dict[str, Any]:
    xml_content = create_profile_xml(ssid, password)
    tmp_path = os.path.join(tempfile.gettempdir(), f"wlan_profile_{ssid}.xml")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        args = ["wlan", "add", "profile", f"filename={tmp_path}"]
        if interface:
            args += [f"interface={interface}"]
        result = _run_netsh(args)
        logger.info(
            "Add profile %s: %s", ssid, result["success"],
            extra={"action": "add_profile", "step": "result"},
        )
        return result
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def connect(ssid: str, interface: Optional[str] = None) -> dict[str, Any]:
    args = ["wlan", "connect", f"name={ssid}"]
    if interface:
        args += [f"interface={interface}"]
    result = _run_netsh(args)
    logger.info(
        "Connect to %s: %s", ssid, result["success"],
        extra={"action": "connect", "step": "result"},
    )
    return result


def disconnect(interface: Optional[str] = None) -> dict[str, Any]:
    args = ["wlan", "disconnect"]
    if interface:
        args += [f"interface={interface}"]
    return _run_netsh(args)


def get_interfaces() -> dict[str, Any]:
    result = _run_netsh(["wlan", "show", "interfaces"])
    interfaces = []
    if result["success"] and result["stdout"]:
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
    result["interfaces"] = interfaces
    return result


def get_connected_ssid(interface: Optional[str] = None) -> Optional[str]:
    info = get_interfaces()
    for iface in info.get("interfaces", []):
        if interface and iface.get("name") != interface:
            continue
        if iface.get("state", "").lower() in ("connected", "已連線"):
            return iface.get("ssid", iface.get("profile"))
    return None


def get_ipv4_and_gateway(interface_name: Optional[str] = None) -> dict[str, Optional[str]]:
    args = ["interface", "ip", "show", "address"]
    if interface_name:
        args.append(f"name={interface_name}")
    result = _run_netsh(args)
    ipv4 = None
    gateway = None
    if result["success"]:
        for line in result["stdout"].splitlines():
            line_lower = line.strip().lower()
            if "ip address" in line_lower or "ip 位址" in line_lower:
                match = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if match:
                    ipv4 = match.group(1)
            if "default gateway" in line_lower or "預設閘道" in line_lower:
                match = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if match:
                    gateway = match.group(1)
    return {"ipv4": ipv4, "gateway": gateway}


def scan_networks(interface: Optional[str] = None) -> dict[str, Any]:
    args = ["wlan", "show", "networks"]
    if interface:
        args += [f"interface={interface}"]
    args.append("mode=bssid")
    result = _run_netsh(args)
    networks = []
    if result["success"]:
        current: dict[str, str] = {}
        for line in result["stdout"].splitlines():
            line = line.strip()
            if line.startswith("SSID") and "BSSID" not in line:
                if current:
                    networks.append(current)
                parts = line.split(":", 1)
                current = {"ssid": parts[1].strip() if len(parts) > 1 else ""}
            elif ":" in line and current:
                key, _, val = line.partition(":")
                current[key.strip().lower()] = val.strip()
        if current:
            networks.append(current)
    result["networks"] = networks
    return result
