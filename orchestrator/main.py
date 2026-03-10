from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import yaml
from dotenv import load_dotenv

from orchestrator.logging.json_logger import get_logger
from orchestrator.workflow_schema import Workflow, Step
from orchestrator.actions import wifi_remote, wifi_local, router_netgear

logger = get_logger("orchestrator")


def load_workflow(path: str) -> Workflow:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Workflow(**data)


async def execute_step(step: Step, workflow: Workflow, env: dict[str, str]) -> dict[str, Any]:
    logger.info(
        "Executing step: %s (%s)", step.action, step.description or "",
        extra={"action": "execute_step", "step": step.action},
    )

    if step.action == "router_apply":
        router_cfg = step.router or workflow.router
        wifi_cfg = step.wifi or workflow.wifi
        if not router_cfg or not wifi_cfg:
            return {"success": False, "error": "Missing router or wifi config"}
        return await router_netgear.apply_router_settings(
            base_url=router_cfg.base_url,
            user=env.get("ROUTER_USER", "admin"),
            password=env.get("ROUTER_PASS", ""),
            ssid=wifi_cfg.ssid,
            wifi_password=wifi_cfg.password,
            channel=router_cfg.channel,
            bands=router_cfg.bands,
        )

    elif step.action == "wifi_connect_remote":
        wifi_cfg = step.wifi or workflow.wifi
        workers = step.workers or workflow.workers
        if not wifi_cfg or not workers:
            return {"success": False, "error": "Missing wifi or workers config"}
        worker_urls = [w.url for w in workers]
        return await wifi_remote.connect_multiple(
            worker_urls, wifi_cfg.ssid, wifi_cfg.password, wifi_cfg.interface
        )

    elif step.action == "wifi_connect_local":
        wifi_cfg = step.wifi or workflow.wifi
        if not wifi_cfg:
            return {"success": False, "error": "Missing wifi config"}
        return wifi_local.connect_local(
            wifi_cfg.ssid, wifi_cfg.password, wifi_cfg.interface
        )

    elif step.action == "wait":
        seconds = step.wait_seconds or 5
        logger.info("Waiting %.1f seconds", seconds, extra={"action": "wait", "step": "sleeping"})
        await asyncio.sleep(seconds)
        return {"success": True, "waited": seconds}

    else:
        return {"success": False, "error": f"Unknown action: {step.action}"}


async def run_workflow(workflow: Workflow) -> dict[str, Any]:
    load_dotenv()
    env = {
        "ROUTER_USER": os.environ.get("ROUTER_USER", "admin"),
        "ROUTER_PASS": os.environ.get("ROUTER_PASS", ""),
    }

    results: list[dict[str, Any]] = []
    overall_success = True

    for i, step in enumerate(workflow.steps):
        logger.info(
            "Step %d/%d: %s", i + 1, len(workflow.steps), step.action,
            extra={"action": "run_workflow", "step": f"step_{i+1}"},
        )
        result = await execute_step(step, workflow, env)
        result["step_index"] = i
        result["action"] = step.action
        results.append(result)

        if not result.get("success", False) and step.action != "wait":
            overall_success = False
            logger.error(
                "Step %d failed, stopping workflow", i + 1,
                extra={"action": "run_workflow", "step": "abort"},
            )
            break

    report = {
        "workflow": workflow.name,
        "success": overall_success,
        "steps": results,
    }
    return report


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.main <workflow.yaml>")
        sys.exit(1)

    workflow_path = sys.argv[1]
    workflow = load_workflow(workflow_path)
    report = asyncio.run(run_workflow(workflow))

    os.makedirs("artifacts", exist_ok=True)
    out_path = os.path.join("artifacts", "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(json.dumps(report, indent=2, default=str))
    sys.exit(0 if report["success"] else 1)


if __name__ == "__main__":
    main()
