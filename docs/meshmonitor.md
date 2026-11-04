# MeshMonitor Integration

Orbit and Launch Alert runs as a [MeshMonitor](https://github.com/Yeraze/MeshMonitor) user script. MeshMonitor delivers replies and alerts over **Meshtastic**, **MeshCore**, and any other mesh network it supports. Orbit and Launch Alert never transmits on a radio itself.

It supports two MeshMonitor features:

| Feature | Started when | What the script prints |
|---|---|---|
| **Auto Responder** (on-demand `!ola` commands) | MeshMonitor sets `MESSAGE` | Exactly one JSON object: `{"response": "..."}` |
| **Timer Triggers** (automatic alerts on a cron schedule) | MeshMonitor sets `TIMER_NAME` (no `MESSAGE`) | `{"response": ""}` when nothing is due, otherwise `{"response": "..."}` or `{"responses": ["...", ...]}` (max 5) |

Diagnostics go to stderr only, so stdout always holds valid JSON. Each message is at most 200 characters (`OLA_MAX_LEN`), the limit MeshMonitor applies.

---

## 1. Copy the script

**Using the UI (recommended):** Settings → Automation → Auto Responder → Script Management → **Import Script** → select `orbit_and_launch_alert.py`.

**Manual:**

```bash
docker cp orbit_and_launch_alert.py meshmonitor:/data/scripts/
docker exec meshmonitor chmod +x /data/scripts/orbit_and_launch_alert.py
```

The `mm_meta` header gives it the name **🚀 Orbit and Launch Alert** in MeshMonitor's script lists, plus `version` and `source` so **Settings → Scripts → Check for updates** can find new releases.

## 2. Install `sgp4` (for satellite and ISS passes)

Launch alerts need nothing extra. For satellite predictions, install `sgp4` into a folder next to the script, so it survives container upgrades:

```bash
docker exec meshmonitor /opt/apprise-venv/bin/pip install --target /data/scripts/ola_vendor sgp4
```

The script automatically adds `/data/scripts/ola_vendor` to its import path (`OLA_VENDOR_DIR`). On Alpine, pip installs sgp4's pure-Python wheel, which is fast enough (well under a second for 20 satellites). `!ola status` shows `sgp4 ok` when it is found.

## 3. Configure environment variables

Add `OLA_*` variables to the MeshMonitor service in `docker-compose.yml`:

```yaml
services:
  meshmonitor:
    image: yeraze/meshmonitor:latest
    volumes:
      - ./scripts:/data/scripts
      - ./data:/data
    environment:
      - TZ=America/New_York
      - OLA_MODE=orbit_and_launch
      - OLA_LAT=25.7816
      - OLA_LON=-80.2220
      - OLA_LAUNCH_SITES=cape
      - OLA_SATELLITE_GROUPS=stations
      - OLA_MIN_ELEVATION_DEG=30
```

Without `OLA_LAT`/`OLA_LON`, the node position MeshMonitor provides (`MM_LAT`/`MM_LON`) is used. Without `OLA_TIMEZONE`, MeshMonitor's `TZ` is used. The cache lives in `/data/scripts/cache` by default.

## 4. Auto Responder trigger

Settings → Automation → Auto Responder → **Add Trigger**:

| Field | Value |
|---|---|
| Trigger | `!ola, !ola {cmd}, !ola {cmd} {arg}` (or one trigger per command) |
| Type | Script |
| Response | `/data/scripts/orbit_and_launch_alert.py` |

The script reads the full message from `MESSAGE` and parses it itself, so `!ola`, `/ola`, `!ola launch`, `!ola sat 25544`, etc. all work through the same script.

### Commands

| Command | Reply |
|---|---|
| `!ola` | Next relevant space event (soonest launch or pass for the mode) |
| `!ola launch` | Next upcoming rocket launch |
| `!ola launches` | Launches in the next 24 hours |
| `!ola iss` | Next ISS overpass |
| `!ola satellites` | Upcoming satellite passes |
| `!ola sat 25544` | Next pass of a specific NORAD ID |
| `!ola visible` | Predicted visible passes (estimate) |
| `!ola status` | Version, mode, cache ages, backoff, sgp4/location state |
| `!ola demo` | Simulated sample alerts |
| `!ola help` | Command list |

## 5. Timer Trigger (automatic alerts)

Open the source's **Automation** tab → **Timer Triggers**:

| Field | Value |
|---|---|
| Name | Orbit and Launch Alert (auto-filled from `mm_meta`) |
| Schedule | `*/5 * * * *` |
| Type | Script |
| Script | `/data/scripts/orbit_and_launch_alert.py` |
| Channel | the channel that should receive alerts |

Each run checks for due launch countdowns, schedule changes, outcomes, and passes starting within `OLA_PASS_ALERT_MINUTES`. The ledger in the cache directory records what has been sent, so a five-minute timer never repeats an alert, even across MeshMonitor restarts. Network refreshes still follow the cache intervals (launches every 15 min, elements every 12 h), which keeps you within provider limits.

If more than 5 alerts are due at once (for example on the very first run), the fifth message summarizes the rest.

## MQTT inside MeshMonitor

MQTT publishing is **off** inside MeshMonitor unless `OLA_MQTT_ALLOW_IN_MESHMONITOR=true`, so a standalone instance and MeshMonitor don't publish the same event twice. Commands that arrived through an MQTT bridge (`VIA_MQTT=true`) never publish, which prevents bridge loops.

## Troubleshooting

| Reply | Fix |
|---|---|
| `⚠️ Config: OLA_LAT: 123 out of range -90..90` | Correct the variable shown |
| `⚠️ Satellite tracking needs sgp4 (pip install sgp4)` | Do step 2 |
| `⚠️ Set OLA_LAT/OLA_LON for pass predictions` | Set a location or give the node a position |
| `⚠️ Launch data unavailable (rate limited)` | Wait. The script backs off and uses the cache. Consider `OLA_LL2_API_KEY`. |
| `… \| CACHED 3H` | The provider is unreachable; the reply uses cached data of that age |
| `⚠️ ISS ELEMENTS EXPIRED (9D) \| refresh needed` | CelesTrak has been unreachable for a while; check network access |

Run with `OLA_DEBUG=true` to write diagnostics to MeshMonitor's logs (stderr).
