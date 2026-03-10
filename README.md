# Windows Remote Automation Agent

Automate Wi-Fi SSID/channel changes on a Netgear Nighthawk router and verify
connectivity from both remote Windows workers and the local machine.

## Architecture

```
orchestrator/       Workflow engine — reads YAML, calls worker/router/local wifi
worker/             FastAPI service deployed on each remote Windows machine
router/             Playwright-based Netgear Nighthawk UI automation
scripts/            CLI entry points for demos and E2E tests
workflows/          YAML workflow definitions
artifacts/          Runtime output (screenshots, traces, results) — auto-generated
```

## Prerequisites

- **Windows 10/11** with a Wi-Fi adapter
- **Python 3.10+**
- **Wired Ethernet** to the Netgear Nighthawk router (changing SSID will drop wireless)
- Router accessible at `http://192.168.1.1` (or configure `--base-url`)
- Admin privileges (needed for `netsh wlan` commands)

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install
```

## Configuration

```powershell
Copy-Item .env.example .env
# Edit .env and fill in:
#   ROUTER_USER=admin
#   ROUTER_PASS=<your router password>
```

> **Never commit `.env`.** It is listed in `.gitignore`.

## Usage

### Phase 1 — Worker API (on each remote machine)

Start the worker:

```powershell
uvicorn worker.app:app --host 0.0.0.0 --port 8080
```

Or use the helper script:

```powershell
.\scripts\run_worker.ps1 -Port 8080
```

API endpoints:

| Method | Path            | Description                          |
|--------|-----------------|--------------------------------------|
| POST   | /wifi/connect   | Connect to SSID (body: ssid, password, interface?) |
| GET    | /wifi/status    | Current Wi-Fi connection status      |
| GET    | /wifi/scan      | Scan available networks              |

### Phase 1 — Demo: parallel multi-worker connect

```powershell
python scripts/demo_connect.py `
  --workers http://host1:8080,http://host2:8080 `
  --ssid RFLabTest `
  --password password
```

Output: `artifacts/demo_report.json`

### Phase 2 — Router apply + local Wi-Fi test

```powershell
# 1. Copy .env.example -> .env and fill ROUTER_USER / ROUTER_PASS
# 2. Run:
python scripts/router_apply_and_test_local_wifi.py `
  --ssid RFLabTest `
  --password password
```

This will:

1. Open the Netgear Nighthawk web UI via Playwright
2. Log in (credentials from `.env`)
3. Navigate to Wireless Settings
4. Set SSID and password on 2.4G and 5G bands
5. Click Apply and poll until router is reachable again
6. Connect the local Wi-Fi adapter to the new SSID via `netsh wlan`
7. Verify: SSID matches, IPv4 assigned, default gateway pingable
8. Write `artifacts/result.json`

### Full workflow (orchestrator)

```powershell
python -m orchestrator.main workflows/sample.yaml
```

## Artifacts

All runtime artifacts are saved to `artifacts/` (never committed):

| File                       | Description                           |
|----------------------------|---------------------------------------|
| `result.json`              | Final test result                     |
| `demo_report.json`         | Phase 1 multi-worker report           |
| `screenshot_*.png`         | Browser screenshots on failure        |
| `page_*.html`              | HTML dump of router page on failure   |
| `trace*.zip`               | Playwright trace archive              |
| `network.har`              | Network traffic log (HAR format)      |

## Limitations

- **Wired connection required:** The orchestrator machine must be connected to the
  router via Ethernet. Changing the SSID will disconnect any existing Wi-Fi link.
- **Admin privileges:** `netsh wlan` commands require administrator access.
- **Router firmware:** The Playwright selectors use label-based strategies to
  tolerate firmware UI variations, but may need adjustment for significantly
  different Netgear firmware versions.
- **Single router:** MVP targets a single Netgear Nighthawk at a fixed IP.

## Testing

```powershell
# Verify worker starts
uvicorn worker.app:app --host 127.0.0.1 --port 8080

# In another terminal, check status
Invoke-RestMethod http://127.0.0.1:8080/wifi/status

# Full E2E (requires router + wired connection)
python scripts/router_apply_and_test_local_wifi.py --ssid RFLabTest --password password
```
