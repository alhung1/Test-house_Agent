"""CLI entry point for the Phase 3 multi-band channel sweep.

Usage::

    python scripts/run_sweep_lab.py
    python scripts/run_sweep_lab.py --workflow workflows/sweep_lab.yaml
    python scripts/run_sweep_lab.py --continue-on-failure --target-ping-ip 10.0.0.1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.sweep_runner import load_sweep_workflow, run_sweep


def _apply_overrides(sweep_wf, args):
    """Patch the loaded sweep workflow with CLI overrides."""
    if args.base_url:
        sweep_wf.router.base_url = args.base_url
    if args.target_ping_ip:
        sweep_wf.sweep.target_ping_ip = args.target_ping_ip
    if args.continue_on_failure:
        sweep_wf.sweep.continue_on_failure = True
    if args.base_ssid:
        sweep_wf.sweep.base_ssid = args.base_ssid
    if args.password:
        sweep_wf.sweep.password = args.password


def main():
    parser = argparse.ArgumentParser(
        description="Phase 3: One-click multi-band channel sweep",
    )
    parser.add_argument(
        "--workflow", default="workflows/sweep_lab.yaml",
        help="Path to sweep workflow YAML (default: workflows/sweep_lab.yaml)",
    )
    parser.add_argument("--base-url", default=None, help="Override router base URL")
    parser.add_argument("--target-ping-ip", default=None, help="Override ping gate target IP")
    parser.add_argument("--continue-on-failure", action="store_true",
                        help="Keep sweeping even if an iteration fails")
    parser.add_argument("--base-ssid", default=None, help="Override base SSID prefix")
    parser.add_argument("--password", default=None, help="Override Wi-Fi password")
    args = parser.parse_args()

    sweep_wf = load_sweep_workflow(args.workflow)
    _apply_overrides(sweep_wf, args)

    summary = asyncio.run(run_sweep(sweep_wf))

    print(json.dumps(summary, indent=2, default=str))
    sys.exit(0 if summary.get("success") else 1)


if __name__ == "__main__":
    main()
