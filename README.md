<p align="center">
  <img src="docs/assets/logo.png" alt="Orbit and Launch Alert Logo" width="200"/>
</p>

<p align="center">
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.8%2B-blue" alt="Python Version">
  </a>
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  </a>
  <a href="https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert/releases">
    <img src="https://img.shields.io/badge/Release-v1.0.0-orange" alt="Release">
  </a>
  <a href="https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert/actions/workflows/tests.yml">
    <img src="https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert/actions/workflows/tests.yml/badge.svg" alt="Tests">
  </a>
</p>

# 🚀 🛰️ Orbit and Launch Alert

**Orbit and Launch Alert** is a lightweight Python [**MeshMonitor**](https://github.com/Yeraze/MeshMonitor) script that provides **emoji-based rocket launch notifications**, **satellite overpass predictions**, and **ISS visibility alerts** over [**Meshtastic**](https://meshtastic.org/), [**MeshCore**](https://meshcore.io), or any other mesh network MeshMonitor supports, using **official launch schedules** and **orbital data**.

MeshMonitor handles **Meshtastic, MeshCore, routing, channels, and delivery**.
Orbit and Launch Alert focuses purely on **event detection, orbital prediction, filtering, and alert formatting**.

No scraping.
No shared API keys.
No direct radio transmission.

It is the space-monitoring companion to [Sky and Sea Alert](https://github.com/maxhayim/meshmonitor-skyandsea-alert) (aircraft and vessels). The two scripts are independent: run either one, or both.

---

## How It Works

```
The Space Devs Launch Library 2 (launch schedules)  +  CelesTrak GP data (orbital elements)
                                   ↓
                        Orbit and Launch Alert
      (event detection, local SGP4 orbital prediction, filtering, formatting)
                                   ↓
                              MeshMonitor
                                   ↓
                     Meshtastic / MeshCore / other networks
```

- **Rocket launches** come from an external, authoritative schedule: [The Space Devs Launch Library 2](https://thespacedevs.com/llapi).
- **Satellite passes are computed locally.** Orbital elements are downloaded from [CelesTrak](https://celestrak.org/) and cached, then propagated on your machine with the MIT-licensed [`sgp4`](https://pypi.org/project/sgp4/) library. No tracking service is asked "when is the next pass".
- **MeshMonitor** sends the replies and scheduled alerts over the radio.

---

## Quick Start (5-minute setup)

### 1) Clone or download

```bash
git clone https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert.git
cd meshmonitor-orbitandlaunch-alert
```

Or download `orbit_and_launch_alert.py` directly.

---

### 2) Run demo mode (no internet, no APIs, no hardware)

```bash
export OLA_MODE=demo
python3 orbit_and_launch_alert.py
```

You should immediately see sample 🚀, 🛰️ and 🌌 alerts, each marked `🧪 DEMO`.
This verifies your environment and MeshMonitor formatting. Demo data is **simulated**, never live.

---

### 3) Install the orbit library (for satellite passes)

```bash
pip install -r requirements.txt     # installs sgp4 (MIT license)
```

Launch alerts work without it. Satellite and ISS predictions need it.

---

### 4) Set your location and time zone

```bash
export OLA_LAT=25.7816
export OLA_LON=-80.2220
export OLA_TIMEZONE=America/New_York
```

Inside MeshMonitor, the script falls back to the connected node's position (`MM_LAT`/`MM_LON`) and the server `TZ` when these are not set.

---

### 5) Run live mode (choose one)

#### Option A — Launches and passes (recommended)

```bash
export OLA_MODE=orbit_and_launch
python3 orbit_and_launch_alert.py
```

#### Option B — Rocket launches only

```bash
export OLA_MODE=launches
export OLA_LAUNCH_SITES=cape          # optional: Cape Canaveral / Kennedy only
python3 orbit_and_launch_alert.py
```

#### Option C — ISS only

```bash
export OLA_MODE=iss
python3 orbit_and_launch_alert.py
```

#### Option D — Satellite groups or NORAD IDs

```bash
export OLA_MODE=satellites
export OLA_SATELLITE_GROUPS=amateur,noaa
export OLA_SATELLITE_IDS=25544,48274
python3 orbit_and_launch_alert.py
```

Add `--once` to run a single check (handy for cron).

---

## Features

- 🚀 Worldwide rocket launch tracking (NASA, SpaceX, ULA, Blue Origin, Rocket Lab, Arianespace, JAXA, ISRO, and every other provider Launch Library 2 lists)
- ⏱️ Countdown alerts at configurable thresholds (default T-24H, T-1H, T-15M, T-0), each one individually configurable
- ⚠️ Delay, reschedule, hold, cancellation, and "removed from schedule" alerts
- ✅ Success / failure / partial-failure alerts **only when Launch Library 2 reports them**
- 🌎 Launch filters by country, agency, launch site (with a `cape` preset), rocket, or mission
- 📏 Optional distance from your location to the launch pad (distance only; it never claims you can see the launch)
- 🛰️ Local SGP4 pass predictions: AOS, maximum elevation and its time, LOS, duration, azimuths
- 🌌 Visible-pass estimates (dark sky + sunlit satellite), clearly labeled as estimates
- 📡 Amateur-radio satellites by group or NORAD ID (AOS, LOS, max elevation, duration; no invented frequencies)
- 🧾 Persistent notification ledger: restarts never resend an alert
- 💾 Atomic, bounded cache with freshness labels; works offline from valid cached data
- 🔒 Rate-limit aware: backoff on HTTP 429 / throttling, CelesTrak download etiquette respected
- 📨 Optional MQTT event publishing (off by default)
- 🧪 Complete offline demo mode

---

## Supported Data Sources (v1.x)

### 🚀 Rocket launches: The Space Devs Launch Library 2 (official API)

- API: `https://ll.thespacedevs.com/2.3.0` (override with `OLA_LL2_BASE`)
- Endpoints used: `/launches/upcoming/` and `/launches/previous/` with `mode=normal`
- Anonymous access is **rate limited**. The script refreshes the schedule every 15 minutes by default and backs off when throttled. It never polls every minute.
- Optional: set `OLA_LL2_API_KEY` to your own Launch Library 2 token for higher limits. Never share keys.

```bash
export OLA_LAUNCH_PROVIDER=launchlibrary2
```

### 🛰️ Orbital elements: CelesTrak GP data (official, free)

- Service: `https://celestrak.org/NORAD/elements/gp.php` with `FORMAT=json` (OMM)
- Queries: `GROUP=<name>` (e.g. `stations`, `amateur`, `noaa`, `weather`, `starlink`, `visual`) or `CATNR=<NORAD ID>`
- Elements are cached and refreshed at most every 12 hours by default (CelesTrak updates about every 2 hours and asks clients not to download the same data more often)
- Propagation runs locally with `sgp4`

```bash
export OLA_SATELLITE_PROVIDER=celestrak
```

See [docs/providers.md](docs/providers.md) for details, limits, and what is not supported.

---

## Operating Modes

- demo — simulated sample alerts, no data sources (default)
- launches — rocket launches only
- satellites — passes for `OLA_SATELLITE_GROUPS` and `OLA_SATELLITE_IDS`
- iss — ISS passes and visible-pass estimates only
- orbit_and_launch — launches plus satellite passes (ISS if no satellites are configured)

---

## Requirements

- Python 3.8+ (Python 3.9+ recommended for named time zones; on 3.8 install `backports.zoneinfo` or use `OLA_TIMEZONE=UTC`)
- [`sgp4`](https://pypi.org/project/sgp4/) for satellite predictions (optional for launch-only use)
- Internet access for live data (not needed for demo mode or for cached data)

No Docker required.
No scraping.
No shared credentials.
Runs on Linux, Raspberry Pi, macOS, and inside the MeshMonitor Docker container.

---

## Configuration (Environment Variables)

Core:

```bash
OLA_MODE=orbit_and_launch
OLA_LAT=25.7816
OLA_LON=-80.2220
OLA_TIMEZONE=America/New_York
OLA_POLL_INTERVAL=300
OLA_SUPPRESS_MINUTES=15
OLA_CACHE_DIR=./cache
OLA_HTTP_TIMEOUT=10
```

Launches:

```bash
OLA_LAUNCH_PROVIDER=launchlibrary2
OLA_LAUNCH_ALERT_HOURS=24,1
OLA_LAUNCH_ALERT_MINUTES=15,0
OLA_LAUNCH_COUNTRIES=USA
OLA_LAUNCH_SITES=cape
```

Satellites:

```bash
OLA_SATELLITE_PROVIDER=celestrak
OLA_SATELLITE_GROUPS=stations
OLA_SATELLITE_IDS=25544
OLA_MIN_ELEVATION_DEG=30
OLA_LOOKAHEAD_HOURS=24
```

Every variable, with defaults and validation rules, is documented in [docs/configuration.md](docs/configuration.md). A complete template is in [.env.example](.env.example).

Invalid latitude, longitude, time zone, NORAD IDs, modes, or numbers are **reported, never silently replaced** with defaults.

---

## MeshMonitor Auto Responder Integration

Orbit and Launch Alert is fully compatible with MeshMonitor Auto Responder scripting.

Commands:

| Command | Function |
|---|---|
| `!ola` | Next relevant space event |
| `!ola launch` | Next upcoming rocket launch |
| `!ola launches` | Upcoming launch summary (next 24h) |
| `!ola iss` | Next ISS overpass |
| `!ola satellites` | Upcoming satellite passes |
| `!ola sat 25544` | Passes for a specific NORAD catalog ID |
| `!ola visible` | Predicted visible passes |
| `!ola status` | Data source and cache status |
| `!ola demo` | Demonstration alerts |
| `!ola help` | Available commands |

`/ola` works as an alias for `!ola`.

Output (exactly one JSON object on stdout):

```json
{"response":"🛰️ ISS | 20:15 EST | MAX 65° | 6MIN | NW→SE"}
```

Example alerts (formats only, not real events):

```
🚀 FALCON 9 | KSC | T-1H
🚀 STARSHIP | STARBASE TX | T-15M
⚠️ LAUNCH DELAYED | FALCON 9 | Starlink 10-12 | NEW Thu 18:30 EST | T-1D2H
🛰️ ISS | 20:15 EST | MAX 65° | 6MIN | NW→SE
🛰️ NOAA 19 | AOS 14:22 EST | MAX 48° | LOS 14:35 EST | 13MIN | N→S | #33591
🌌 ISS VISIBLE | 20:15 EST | MAX 65° | 6MIN | NW→SE | EST. NOT GUARANTEED
🚀 No launches in next 24h
```

### Automatic alerts (MeshMonitor Timer Triggers)

MeshMonitor's **Timer Triggers** run a script on a cron schedule and send its output to a channel. When started by a Timer Trigger, Orbit and Launch Alert runs one notification check and prints:

- `{"response": ""}` when nothing is due (MeshMonitor sends nothing), or
- `{"response": "..."}` / `{"responses": ["...", "..."]}` with new alerts.

The persistent ledger in `OLA_CACHE_DIR` makes sure each alert goes out once. A schedule of `*/5 * * * *` works well. Launch data is still fetched at most every 15 minutes.

You can also run the script standalone (`python3 orbit_and_launch_alert.py`) as a long-running poller that prints alerts and optionally publishes them to MQTT.

MeshMonitor handles:
- Meshtastic, MeshCore, and any other mesh network it supports
- Routing
- Channels
- Retries

Orbit and Launch Alert never transmits on radios directly.

Step-by-step setup, including installing `sgp4` inside the MeshMonitor container: [docs/meshmonitor.md](docs/meshmonitor.md). More example output: [docs/examples.md](docs/examples.md).

---

## MQTT (optional)

Disabled unless `OLA_MQTT_HOST` is set. Uses `mosquitto_pub`, the same approach as Sky and Sea Alert. Inside MeshMonitor it stays off unless `OLA_MQTT_ALLOW_IN_MESHMONITOR=true`, and it never republishes a command that arrived over an MQTT bridge (`VIA_MQTT=true`), which prevents feedback loops.

```json
{
  "source": "orbit-and-launch-alert",
  "version": "v1.0.0",
  "ts": "2026-11-04T12:00:00Z",
  "type": "satellite_pass",
  "mode": "satellites",
  "geo": {"lat": 25.7816, "lon": -80.2220},
  "message": "🛰️ ISS | 20:15 EST | MAX 65°",
  "data": {}
}
```

(Schema example only, not a real predicted pass.) Event types: `launch_alert`, `launch_change`, `launch_status`, `launch_outcome`, `iss_pass`, `satellite_pass`, `visible_pass`, `demo`.

---

## 🌍 Remote Access (VPN)

If you host MeshMonitor, a cache volume, or an MQTT broker at home, reach them securely with a VPN such as **Tailscale**: no port forwarding, encrypted, and it works behind NAT / CGNAT. Point `OLA_MQTT_HOST` at a Tailscale IP or MagicDNS name. Tailscale is optional.

---

## Known Limitations

- Visibility is an **estimate**. It uses the solar altitude at your location (sky darker than civil twilight, sun ≤ −6°) and a cylindrical Earth-shadow model. It ignores weather, light pollution, terrain, and satellite brightness. It never promises naked-eye visibility.
- Distance to a launch pad is **distance only**. It does not mean the launch is visible from your location.
- Launch outcomes are reported only when Launch Library 2 marks a launch Success, Failure, Partial Failure, or In Flight. "T-0" alerts say *scheduled* time, not liftoff.
- Launch Library 2 has no "canceled" status today. Launches that disappear from the schedule are reported as **removed from schedule**, not as canceled.
- Pass predictions get less accurate as orbital elements age. Elements older than `OLA_MAX_ELEMENT_AGE_DAYS` (default 7) are refused.
- Large CelesTrak groups (e.g. `starlink`) are capped at `OLA_MAX_SATELLITES` (default 20) objects per group to stay within MeshMonitor's 30-second script timeout.
- Frequencies for amateur satellites are not included. Any future frequency data must come from a verified source.

---

## Not Directly Supported (Documented Only)

The following exist but are not integrated, because they require scraping, commercial agreements, or a proprietary pass service:

- Next Spaceflight (website)
- RocketLaunch.live (separate API terms)
- N2YO, Heavens-Above, and other hosted pass-prediction services
- Space-Track.org (account required; CelesTrak redistributes the same public GP data)
- SatNOGS DB (possible future source for verified amateur frequencies)

Orbit and Launch Alert does not scrape websites or bypass terms of service.

---

## Tests

```bash
pip install -r requirements.txt pytest
python3 -m pytest -q
```

The test suite runs entirely offline using recorded-style fixtures and mocked provider responses.

---

## License

This project is licensed under the MIT License.

See the [LICENSE](LICENSE) file for details.
Full license text: https://opensource.org/licenses/MIT

Dependency: [`sgp4`](https://github.com/brandon-rhodes/python-sgp4) by Brandon Rhodes, MIT License.

---

## Contributing

Pull requests are welcome. Open an issue first to discuss ideas or report bugs. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Related Projects

- [Sky and Sea Alert](https://github.com/maxhayim/meshmonitor-skyandsea-alert): companion MeshMonitor script for aircraft overhead and vessels nearby

---

## Acknowledgments

* MeshMonitor built by [Yeraze](https://github.com/Yeraze)
* [The Space Devs](https://thespacedevs.com/) for Launch Library 2
* [CelesTrak](https://celestrak.org/) (Dr. T.S. Kelso) for public GP orbital data
* [python-sgp4](https://github.com/brandon-rhodes/python-sgp4) by Brandon Rhodes
* Companion to [Sky and Sea Alert](https://github.com/maxhayim/meshmonitor-skyandsea-alert)

Meshtastic® is a registered trademark of Meshtastic LLC. MeshCore, NASA, SpaceX, and other names are trademarks of their respective owners. This project is not affiliated with or endorsed by them.

Discover other community-contributed scripts for MeshMonitor: https://meshmonitor.org/user-scripts.html
