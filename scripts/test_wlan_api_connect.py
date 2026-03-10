"""Test connecting to Wi-Fi using Windows WLAN API via ctypes.

This bypasses the netsh location permission requirement by using
wlanapi.dll directly.
"""
import ctypes
import ctypes.wintypes
import subprocess
import sys
import time

wlanapi = ctypes.windll.wlanapi

WLAN_API_VERSION_2_0 = 2

ERROR_SUCCESS = 0

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.wintypes.DWORD),
        ("Data2", ctypes.wintypes.WORD),
        ("Data3", ctypes.wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", GUID),
        ("strInterfaceDescription", ctypes.c_wchar * 256),
        ("isState", ctypes.c_uint),
    ]

class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", ctypes.wintypes.DWORD),
        ("dwIndex", ctypes.wintypes.DWORD),
        ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
    ]

class DOT11_SSID(ctypes.Structure):
    _fields_ = [
        ("uSSIDLength", ctypes.c_ulong),
        ("ucSSID", ctypes.c_ubyte * 32),
    ]

class WLAN_CONNECTION_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("wlanConnectionMode", ctypes.c_uint),
        ("strProfile", ctypes.c_wchar_p),
        ("pDot11Ssid", ctypes.POINTER(DOT11_SSID)),
        ("pDesiredBssidList", ctypes.c_void_p),
        ("dot11BssType", ctypes.c_uint),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def connect_to_ssid(profile_name: str) -> int:
    """Connect to a Wi-Fi network using its profile name via WLAN API."""
    handle = ctypes.wintypes.HANDLE()
    negotiated_version = ctypes.wintypes.DWORD()

    ret = wlanapi.WlanOpenHandle(
        WLAN_API_VERSION_2_0,
        None,
        ctypes.byref(negotiated_version),
        ctypes.byref(handle),
    )
    if ret != ERROR_SUCCESS:
        print(f"WlanOpenHandle failed: {ret}")
        return ret

    iface_list_ptr = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
    ret = wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(iface_list_ptr))
    if ret != ERROR_SUCCESS:
        print(f"WlanEnumInterfaces failed: {ret}")
        wlanapi.WlanCloseHandle(handle, None)
        return ret

    iface_list = iface_list_ptr.contents
    print(f"Found {iface_list.dwNumberOfItems} interface(s)")

    if iface_list.dwNumberOfItems == 0:
        print("No wireless interfaces found")
        wlanapi.WlanFreeMemory(iface_list_ptr)
        wlanapi.WlanCloseHandle(handle, None)
        return -1

    iface_guid = iface_list.InterfaceInfo[0].InterfaceGuid
    iface_desc = iface_list.InterfaceInfo[0].strInterfaceDescription
    print(f"Using interface: {iface_desc}")

    ssid = DOT11_SSID()
    ssid_bytes = profile_name.encode("utf-8")
    ssid.uSSIDLength = len(ssid_bytes)
    for idx, b in enumerate(ssid_bytes):
        ssid.ucSSID[idx] = b

    # wlanConnectionMode: 0 = profile, 1 = temporary, 2 = discovery
    conn_params = WLAN_CONNECTION_PARAMETERS()
    conn_params.wlanConnectionMode = 0  # profile mode
    conn_params.strProfile = profile_name
    conn_params.pDot11Ssid = ctypes.pointer(ssid)
    conn_params.pDesiredBssidList = None
    conn_params.dot11BssType = 1  # infrastructure
    conn_params.dwFlags = 0

    ret = wlanapi.WlanConnect(
        handle,
        ctypes.byref(iface_guid),
        ctypes.byref(conn_params),
        None,
    )

    if ret == ERROR_SUCCESS:
        print(f"WlanConnect succeeded for '{profile_name}'")
    else:
        print(f"WlanConnect failed with error {ret}")

    wlanapi.WlanFreeMemory(iface_list_ptr)
    wlanapi.WlanCloseHandle(handle, None)
    return ret


if __name__ == "__main__":
    ssid = sys.argv[1] if len(sys.argv) > 1 else "RFLabTest"
    print(f"Connecting to '{ssid}'...")
    result = connect_to_ssid(ssid)
    if result == ERROR_SUCCESS:
        print("Waiting 8 seconds for connection to establish...")
        time.sleep(8)
        # Check connection using PowerShell
        r = subprocess.run(
            ["powershell", "-Command", "Get-NetConnectionProfile -InterfaceAlias 'Wi-Fi' | Select-Object Name"],
            capture_output=True, text=True
        )
        print(f"Current connection:\n{r.stdout}")
    sys.exit(0 if result == ERROR_SUCCESS else 1)
