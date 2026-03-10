from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class WifiConfig(BaseModel):
    ssid: str
    password: str
    interface: Optional[str] = None


class RouterConfig(BaseModel):
    base_url: str = "http://192.168.1.1"
    bands: list[str] = ["2.4G", "5G"]
    channel: Optional[str] = None


class WorkerTarget(BaseModel):
    url: str
    name: Optional[str] = None


class Step(BaseModel):
    action: str  # "router_apply" | "wifi_connect_remote" | "wifi_connect_local" | "wait"
    wifi: Optional[WifiConfig] = None
    router: Optional[RouterConfig] = None
    workers: Optional[list[WorkerTarget]] = None
    wait_seconds: Optional[float] = None
    description: Optional[str] = None


class Workflow(BaseModel):
    name: str
    description: Optional[str] = None
    wifi: Optional[WifiConfig] = None
    router: Optional[RouterConfig] = None
    workers: Optional[list[WorkerTarget]] = None
    steps: list[Step]
