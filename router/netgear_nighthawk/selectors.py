"""Netgear Nighthawk-specific selectors derived from actual DOM recon.

The Nighthawk firmware uses:
  - Login: input[name='username'] + input[name='password'] + <a>LOG IN</a>
  - After login: frameset with topframe + formframe
  - Wireless page: WLG_wireless.htm loaded into formframe
  - Per-band fields suffixed: (none) = 2.4G, _an = 5G, _6g = 6G
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BandConfig:
    """Per-band wireless configuration passed by the caller."""
    ssid: str
    password: str
    channel: Optional[str] = None
    security: str = "wpa2"


@dataclass(frozen=True)
class BandSelectors:
    ssid: str
    passphrase: str
    security_radio_name: str
    channel: str
    mode: str


LOGIN_USERNAME = "input[name='username']"
LOGIN_PASSWORD = "input[name='password']"
LOGIN_BUTTON = "a:has-text('LOG IN')"

WIRELESS_PAGE = "/WLG_wireless.htm"

APPLY_BUTTON = "input#apply"
CANCEL_BUTTON = "input#cancel"

SMART_CONNECT_CHECKBOX = "input#enable_smart_connect"

BAND_SELECTORS: dict[str, BandSelectors] = {
    "2.4G": BandSelectors(
        ssid="input[name='ssid']",
        passphrase="input[name='passphrase']",
        security_radio_name="security_type",
        channel="select[name='w_channel']",
        mode="select[name='opmode']",
    ),
    "5G": BandSelectors(
        ssid="input[name='ssid_an']",
        passphrase="input[name='passphrase_an']",
        security_radio_name="security_type_an",
        channel="select[name='w_channel_an']",
        mode="select[name='opmode_an']",
    ),
    "6G": BandSelectors(
        ssid="input[name='ssid_6g']",
        passphrase="input[name='passphrase_6g']",
        security_radio_name="security_type_6g",
        channel="select[name='w_channel_6g']",
        mode="select[name='opmode_6g']",
    ),
}

SECURITY_VALUES = {
    "disable": "Disable",
    "wpa2": "WPA2-PSK",
    "auto": "AUTO-PSK",
    "wpa3": "WPA3-PSK",
    "auto-wpa3": "AUTO-WPA3-PSK",
}
