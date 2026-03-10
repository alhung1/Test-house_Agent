"""CLI entry point for the Phase 2.5 E2E lab workflow.

Usage::

    python scripts/run_e2e_lab.py                              # defaults
    python scripts/run_e2e_lab.py --workflow workflows/e2e_lab.yaml
    python scripts/run_e2e_lab.py --connect-band 5G --target-ping-ip 10.0.0.1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.main import load_workflow, run_workflow
from orchestrator.workflow_schema import PingGateConfig, ScanConfig


def _apply_overrides(workflow, args):
    """Patch the loaded workflow with CLI overrides."""
    if args.base_url and workflow.router:
        workflow.router.base_url = args.base_url

    if args.target_ping_ip:
        for step in workflow.steps:
            if step.action == "ping_gate":
                if step.ping_gate is None:
                    step.ping_gate = PingGateConfig(host=args.target_ping_ip)
                else:
                    step.ping_gate.host = args.target_ping_ip

    if args.connect_band:
        for step in workflow.steps:
            if step.action == "wifi_connect_workers":
                step.connect_band = args.connect_band

    if args.scan_ssid:
        for step in workflow.steps:
            if step.action == "wait_ssid_broadcast":
                if step.scan is None:
                    step.scan = ScanConfig(target_ssid=args.scan_ssid)
                else:
                    step.scan.target_ssid = args.scan_ssid


def main():
    parser = argparse.ArgumentParser(
        description="Phase 2.5: E2E lab workflow runner",
    )
    parser.add_argument(
        "--workflow", default="workflows/e2e_lab.yaml",
        help="Path to workflow YAML (default: workflows/e2e_lab.yaml)",
    )
    parser.add_argument("--base-url", default=None, help="Override router base URL")
    parser.add_argument("--target-ping-ip", default=None, help="Override ping gate target IP")
    parser.add_argument("--connect-band", default=None, help="Override Wi-Fi connect band (e.g. 5G)")
    parser.add_argument("--scan-ssid", default=None, help="Override scan target SSID")
    args = parser.parse_args()

    workflow = load_workflow(args.workflow)
    _apply_overrides(workflow, args)

    report = asyncio.run(run_workflow(workflow))

    print(json.dumps(report, indent=2, default=str))
    sys.exit(0 if report.get("success") else 1)


if __name__ == "__main__":
    main()
