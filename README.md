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
playwright install chromium
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

### Worker API Endpoints

| Method | Path               | Description                                              |
|--------|--------------------|----------------------------------------------------------|
| POST   | /wifi/connect      | Connect to SSID (body: `ssid`, `password`, `interface?`) |
| GET    | /wifi/status       | Current Wi-Fi connection status                          |
| GET    | /wifi/scan         | Scan available networks                                  |
| POST   | /net/ping          | Ping a host (body: `host`, `count?`, `timeout_sec?`)    |
| POST   | /automation/run    | Start a subprocess (body: `command`, `args?`, `cwd?`, `timeout_sec?`) |
| GET    | /automation/status | Check job status (query: `job_id`)                       |

#### POST /net/ping

Runs Windows `ping` against the specified host and returns structured results.

```json
{
  "host": "192.168.1.100",
  "count": 4,
  "timeout_sec": 5
}
```

Response includes `success`, `packets_sent`, `packets_received`, `loss_percent`,
`avg_latency_ms`, `raw_output`, and the path to the saved artifact file.

#### POST /automation/run

Launches a subprocess in the background and returns a `job_id` immediately.

```json
{
  "command": "python",
  "args": ["-c", "print('hello')"],
  "cwd": null,
  "timeout_sec": 300
}
```

#### GET /automation/status?job_id=...

Returns the current state of a background job: `running`, `completed`, `failed`,
or `not_found`, along with `exit_code`, `log_path`, and `elapsed_sec`.

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
python scripts/router_apply_and_test_local_wifi.py `
  --ssid-2g RFLabTest_2G --ch-2g 10 `
  --ssid-5g RFLabTest_5G --ch-5g 44 `
  --ssid-6g RFLabTest_6G --ch-6g 69 `
  --password password
```

This will:

1. Open the Netgear Nighthawk web UI via Playwright
2. Log in (credentials from `.env`)
3. Navigate to Wireless Settings, auto-detect available bands
4. Set per-band SSID, password, and channel
5. Click Apply and poll until router is reachable again
6. Connect the local Wi-Fi adapter to the new SSID via `netsh wlan`
7. Verify: SSID matches, IPv4 assigned, default gateway pingable
8. Write `artifacts/result.json`

### Phase 2.5 — Full E2E Lab Workflow

The E2E workflow orchestrates the entire test sequence across the router and
8 remote worker PCs:

```
Router Apply → SSID Scan → Wi-Fi Connect → Ping Gate → Automation Run → Report
```

**Quick start** (run on the orchestrator PC wired to the router):

```powershell
python scripts/run_e2e_lab.py
```

This reads `workflows/e2e_lab.yaml` by default. Override with CLI flags:

```powershell
python scripts/run_e2e_lab.py `
  --workflow workflows/e2e_lab.yaml `
  --connect-band 5G `
  --target-ping-ip 192.168.1.100 `
  --scan-ssid RFLabTest_5G
```

Or run via the orchestrator module directly:

```powershell
python -m orchestrator.main workflows/e2e_lab.yaml
```

**Workflow steps** (all configurable in YAML):

| Step | Action | Description |
|------|--------|-------------|
| 1 | `router_apply` | Playwright sets per-band SSID/channel on router |
| 2 | `wait_ssid_broadcast` | Poll `GET /wifi/scan` on all workers until SSID visible |
| 3 | `wifi_connect_workers` | Parallel `POST /wifi/connect` on all 8 workers |
| 4 | `ping_gate` | Parallel `POST /net/ping` — gate passes only if ALL workers succeed |
| 5 | `run_automation` | `POST /automation/run` + poll status on target workers |
| 6 | (auto) | Write `artifacts/final_report.json` |

**Worker setup** (on each of the 8 remote PCs):

```powershell
git clone https://github.com/alhung1/Test-house_Agent.git
cd Test-house_Agent
pip install -r requirements.txt
uvicorn worker.app:app --host 0.0.0.0 --port 8080
```

### Phase 3 — Multi-Band Channel Sweep (One-click)

The sweep runner auto-detects which bands the router supports (2.4G, 5G, 6G)
by reading the actual router GUI, then iterates over a configurable
band x channel matrix. Each iteration runs the full E2E pipeline:

```
detect_bands → for each (band, channel):
  router_apply → wait_ssid → connect_workers → ping_gate → (automation noop) → report
→ sweep_summary.json
```

If the router only supports 2.4G + 5G (no 6G on the GUI), the 6G channels
are automatically skipped.

**One-line command** (run on the orchestrator PC):

```powershell
python scripts/run_sweep_lab.py
```

This reads `workflows/sweep_lab.yaml` by default. Override with flags:

```powershell
python scripts/run_sweep_lab.py `
  --workflow workflows/sweep_lab.yaml `
  --continue-on-failure `
  --target-ping-ip 192.168.1.100
```

**Example YAML** (`workflows/sweep_lab.yaml`):

```yaml
name: "Multi-Band Channel Sweep"
router:
  base_url: http://192.168.1.1
workers:
  - {url: "http://192.168.22.221:8080", name: worker-01}
  # ... 8 workers total
sweep:
  base_ssid: RFLabTest
  password: password
  target_ping_ip: "192.168.1.100"
  continue_on_failure: false
  channels:
    "2.4G": [1, 6, 11]
    "5G": [36, 40, 44, 48]
    "6G": [5, 37, 69]
  automation_enabled: false
```

**Sweep behavior:**

- Each iteration sets ALL bands' SSID/password (to prevent the router UI from
  clearing fields), but only changes the channel of the band under test.
- SSIDs use the naming convention `{base_ssid}_2G`, `{base_ssid}_5G`,
  `{base_ssid}_6G`.
- Workers connect to the band being tested (e.g., `RFLabTest_5G` when testing
  5G channel 44).
- `automation_enabled: false` makes the automation step a noop that returns
  `status: "skipped"`. The field structure is preserved in `final_report.json`
  for future use.
- `continue_on_failure: false` (default) stops the sweep at the first failed
  iteration. Set to `true` to sweep all channels regardless.

**Sweep artifacts:**

```
artifacts/
  sweep_summary.json
  sweeps/
    2.4G/
      ch_1/final_report.json
      ch_1/step_router_apply_*.json
      ch_1/step_wait_ssid_*.json
      ...
      ch_6/final_report.json
      ch_11/final_report.json
    5G/
      ch_36/final_report.json
      ch_40/final_report.json
      ...
```

`sweep_summary.json` contains the overall pass/fail count and links to each
per-iteration report.

## Artifacts

All runtime artifacts are saved to `artifacts/` (never committed):

| File                              | Description                               |
|-----------------------------------|-------------------------------------------|
| `final_report.json`              | E2E workflow summary with per-worker results |
| `result.json`                    | Phase 2 single-run result                  |
| `step_*_*.json`                  | Per-step detailed results                  |
| `ping_*.txt`                     | Raw ping output from workers               |
| `automation_*.log`               | Subprocess stdout/stderr from workers      |
| `demo_report.json`              | Phase 1 multi-worker report                |
| `screenshot_*.png`              | Browser screenshots on failure             |
| `page_*.html`                   | HTML dump of router page on failure        |
| `trace*.zip`                    | Playwright trace archive                   |
| `network.har`                   | Network traffic log (HAR format)           |
| `sweep_summary.json`           | Phase 3 sweep overall results              |
| `sweeps/<band>/ch_<N>/...`     | Per-iteration artifacts for channel sweep  |

### final_report.json structure

```json
{
  "workflow": "E2E Lab: ...",
  "success": true,
  "timestamp": "2026-03-09T...",
  "router_apply": {
    "detected_bands": ["2.4G", "5G", "6G"],
    "configured_bands": ["2.4G", "5G", "6G"]
  },
  "workers": {
    "worker-01": {
      "scan_ssid_found": true,
      "connect": { "success": true, "verification": { ... } },
      "ping": { "success": true, "loss_percent": 0.0, "avg_latency_ms": 1.5 },
      "automation": { "status": "completed", "exit_code": 0, "log_path": "..." }
    }
  },
  "failed_step": null,
  "steps": [ ... ],
  "artifacts": [ ... ]
}
```

## Limitations

- **Wired connection required:** The orchestrator machine must be connected to the
  router via Ethernet. Changing the SSID will disconnect any existing Wi-Fi link.
- **Admin privileges:** `netsh wlan` commands require administrator access.
- **Router firmware:** The Playwright selectors are based on Netgear Nighthawk
  firmware. Other models may need new selectors.
- **Single router:** MVP targets a single Netgear Nighthawk at a fixed IP.

## Testing

```powershell
# Verify worker starts
uvicorn worker.app:app --host 127.0.0.1 --port 8080

# In another terminal, check endpoints
Invoke-RestMethod http://127.0.0.1:8080/wifi/status
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/net/ping `
  -Body '{"host":"127.0.0.1"}' -ContentType 'application/json'

# Full E2E (requires router + wired connection + 8 workers)
python scripts/run_e2e_lab.py --workflow workflows/e2e_lab.yaml
```
