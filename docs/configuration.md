# Configuration

Orbit and Launch Alert is configured only through environment variables. Every project-specific variable uses the `OLA_` prefix.

**Validation:** invalid values are never silently replaced with defaults. In standalone mode the script prints each problem and exits with status 2. Inside MeshMonitor it replies `⚠️ Config: <first problem>` so you can see what to fix from the mesh (`!ola help` and `!ola demo` still work).

A complete template is in [`.env.example`](../.env.example).

---

## Core

| Variable | Default | Valid values | Description |
|---|---|---|---|
| `OLA_MODE` | `demo` | `demo`, `launches`, `satellites`, `iss`, `orbit_and_launch` | Operating mode. Aliases: `all`/`orbit-and-launch` → `orbit_and_launch`, `launch` → `launches`, `sats` → `satellites`. |
| `OLA_LAT` | *(unset)* | −90 … 90 | Observer latitude (decimal degrees). Must be set together with `OLA_LON`. |
| `OLA_LON` | *(unset)* | −180 … 180 | Observer longitude (decimal degrees). |
| `OLA_ALT_M` | `0` | −500 … 9000 | Observer altitude above the ellipsoid, metres. |
| `OLA_TIMEZONE` | `TZ`, else `UTC` | IANA name, e.g. `America/New_York` | Time zone for user-facing times. Times are always labeled (`20:15 EST`). Internally everything is ISO 8601 UTC. |
| `OLA_UNITS` | `mi` | `mi`, `km` | Units for launch-pad distance. |
| `OLA_MAX_LEN` | `200` | 60 … 200 | Maximum characters per message. 200 is MeshMonitor's limit, so it cannot be raised above that. |

**Location fallback:** if `OLA_LAT`/`OLA_LON` are not set, the script uses MeshMonitor's `MM_LAT`/`MM_LON` (the connected node's position) when available. Satellite commands reply `⚠️ Set OLA_LAT/OLA_LON for pass predictions` when no location is known.

**Time zone on Python 3.8:** named zones need `zoneinfo` (Python 3.9+) or `pip install backports.zoneinfo`. Otherwise use `OLA_TIMEZONE=UTC`.

---

## Rocket launches

| Variable | Default | Valid values | Description |
|---|---|---|---|
| `OLA_LAUNCH_PROVIDER` | `launchlibrary2` | `launchlibrary2` (`ll2`) | Launch schedule provider. |
| `OLA_LL2_BASE` | `https://ll.thespacedevs.com/2.3.0` | http(s) URL | Launch Library 2 base URL. Use `https://lldev.thespacedevs.com/2.3.0` for development (stale data, relaxed limits). |
| `OLA_LL2_API_KEY` | *(empty)* | token | Optional personal Launch Library 2 token (sent as `Authorization: Token …`). Never commit it. |
| `OLA_LAUNCH_LIMIT` | `25` | 1 … 100 | Number of upcoming launches requested per refresh. |
| `OLA_LAUNCH_REFRESH_MINUTES` | `15` | 5 … 1440 | How long the upcoming schedule is cached before refreshing. The previous-launch list (used for outcomes) refreshes at twice this interval. |
| `OLA_LAUNCH_MAX_AGE_HOURS` | `24` | 1 … 168 | Oldest cached schedule that may still be used when the API is unreachable. Responses built from it end with `CACHED <age>`. |
| `OLA_LAUNCH_ALERT_HOURS` | `24,1` | comma list, 1 … 168 | Countdown alert thresholds in hours. Empty disables hour thresholds. |
| `OLA_LAUNCH_ALERT_MINUTES` | `15,0` | comma list, 0 … 1440 | Countdown alert thresholds in minutes. `0` is the "scheduled T-0" alert. Empty disables minute thresholds. |
| `OLA_ALERT_DELAYS` | `true` | true/false | Alert when a launch's NET moves (later: *delayed*, earlier: *rescheduled*). |
| `OLA_ALERT_HOLDS` | `true` | true/false | Alert when the status changes to *Hold*. |
| `OLA_ALERT_CANCELLATIONS` | `true` | true/false | Alert on a canceled status, or when a launch is removed from the schedule. |
| `OLA_ALERT_OUTCOMES` | `true` | true/false | Alert on Success / Failure / Partial Failure as reported by Launch Library 2. |
| `OLA_ALERT_IN_FLIGHT` | `true` | true/false | Alert when Launch Library 2 marks a launch *In Flight* (source-confirmed liftoff). |
| `OLA_LAUNCH_COUNTRIES` | *(all)* | ISO codes, e.g. `USA,NZ` | Pad country (alpha-2 or alpha-3). |
| `OLA_LAUNCH_AGENCIES` | *(all)* | comma list | Launch provider name or abbreviation (substring, case-insensitive), e.g. `spacex,ula,rocket lab,isro`. |
| `OLA_LAUNCH_SITES` | *(all)* | comma list | Pad or site name substring. Presets: `cape`/`florida` (Cape Canaveral + Kennedy), `ksc`, `ccsfs`, `vandenberg`, `starbase`. |
| `OLA_LAUNCH_ROCKETS` | *(all)* | comma list | Rocket name substring, e.g. `falcon,electron`. |
| `OLA_LAUNCH_MISSIONS` | *(all)* | comma list | Mission name substring, e.g. `starlink,crew`. |
| `OLA_SHOW_DISTANCE` | `true` | true/false | Append the distance from your location to the pad (`190MI AWAY`). Distance only, never a visibility claim. |

Filters combine with **AND** across variables and **OR** within one variable.

### How launch alerts work

- **Thresholds** fire once per launch, per threshold, per NET. If several thresholds are already due when a launch first appears (e.g. first seen at T-50M), only the tightest one is sent. No burst of stale alerts.
- **T-0** means the *scheduled* time has arrived: `🕒 SCHEDULED T-0 | … | LIFTOFF NOT YET CONFIRMED`. It is sent up to 30 minutes after NET.
- **NET changes** re-arm only that launch's countdown alerts and send one `⚠️ LAUNCH DELAYED` or `🔁 LAUNCH RESCHEDULED`.
- **Outcomes** come only from Launch Library 2's own status. Nothing is inferred from the clock.

---

## Satellites

| Variable | Default | Valid values | Description |
|---|---|---|---|
| `OLA_SATELLITE_PROVIDER` | `celestrak` | `celestrak` | Orbital element provider. |
| `OLA_CELESTRAK_GP_URL` | `https://celestrak.org/NORAD/elements/gp.php` | http(s) URL | CelesTrak GP query endpoint. |
| `OLA_SATELLITE_GROUPS` | `stations` | comma list of CelesTrak group names | e.g. `stations`, `amateur`, `noaa`, `weather`, `visual`, `starlink`. Lowercase letters, digits and `-` only. |
| `OLA_SATELLITE_IDS` | *(empty)* | comma list of NORAD IDs | e.g. `25544` (ISS), `48274` (Tiangong), `33591` (NOAA 19). Positive integers, up to 9 digits. |
| `OLA_MIN_ELEVATION_DEG` | `30` | 0 … 89 | Only report passes whose maximum elevation reaches this (common choices: 10, 30, 60). |
| `OLA_LOOKAHEAD_HOURS` | `24` | 1 … 72 | Prediction window for commands. |
| `OLA_GP_REFRESH_HOURS` | `12` | 2 … 168 | How long downloaded elements are reused before refreshing. Minimum 2 h, per CelesTrak's guidance. |
| `OLA_MAX_ELEMENT_AGE_DAYS` | `7` | 0.5 … 30 | Elements whose epoch is older than this are treated as expired and not used for predictions. |
| `OLA_MAX_SATELLITES` | `20` | 1 … 200 | Cap on objects taken from each group, and on objects propagated per request. Keeps MeshMonitor responses inside its 30-second script timeout. |
| `OLA_PASS_ALERT_MINUTES` | `10` | 0 … 720 | Automatic pass alerts are sent when AOS is within this many minutes. |
| `OLA_ISS_VISIBLE_ONLY` | `false` | true/false | For automatic alerts, only announce ISS passes predicted to be visible. |
| `OLA_COMPUTE_BUDGET_SECONDS` | `15` | 1 … 600 | Time budget for pass computation per request. Raise it for big standalone runs. |

`!ola visible` always includes the ISS, adding it if it isn't already in your configuration.

---

## Runtime

| Variable | Default | Valid values | Description |
|---|---|---|---|
| `OLA_POLL_INTERVAL` | `300` | 60 … 86400 | Seconds between checks in standalone mode. Network refreshes still follow the cache intervals above. |
| `OLA_SUPPRESS_MINUTES` | `15` | 0 … 1440 | Suppress identical alert text repeated within this window (on top of the persistent ledger). |
| `OLA_CACHE_DIR` | `./cache` | path | Cache and state directory. Relative paths are resolved against the script's own folder (inside MeshMonitor: `/data/scripts/cache`). |
| `OLA_HTTP_TIMEOUT` | `10` | 1 … 60 | Per-request network timeout, seconds. |
| `OLA_VENDOR_DIR` | `<script dir>/ola_vendor` | path | Extra import path checked for `sgp4` (see [meshmonitor.md](meshmonitor.md)). |
| `OLA_DEBUG` | `false` | true/false | Write diagnostics to stderr (never stdout). |

### Cache contents

| File | Contents | Refresh | Max age when offline |
|---|---|---|---|
| `ll2_upcoming.json` | Normalized upcoming launches | `OLA_LAUNCH_REFRESH_MINUTES` | `OLA_LAUNCH_MAX_AGE_HOURS` |
| `ll2_previous.json` | Recent launches (outcomes) | 2 × refresh | `OLA_LAUNCH_MAX_AGE_HOURS` |
| `gp_group_<name>.json`, `gp_catnr_<id>.json` | OMM element sets | `OLA_GP_REFRESH_HOURS` | 30 days (and element age ≤ `OLA_MAX_ELEMENT_AGE_DAYS`) |
| `state.json` | Notification ledger, launch history, backoff | every run | ledger entries expire after 14 days, max 2000 |

All writes are atomic (temporary file + rename). At most 24 GP files are kept.

---

## MQTT (optional)

| Variable | Default | Description |
|---|---|---|
| `OLA_MQTT_HOST` | *(empty → disabled)* | Broker host. Setting it enables MQTT in standalone mode. |
| `OLA_MQTT_PORT` | `1883` | Broker port. |
| `OLA_MQTT_TOPIC` | `orbit-and-launch-alert/events` | Topic (no wildcards). |
| `OLA_MQTT_USERNAME` | *(empty)* | Optional username. |
| `OLA_MQTT_PASSWORD` | *(empty)* | Optional password. Never put real secrets in committed files. |
| `OLA_MQTT_TLS` | `false` | Pass `--tls-version tlsv1.2` to `mosquitto_pub`. |
| `OLA_MQTT_ALLOW_IN_MESHMONITOR` | `false` | Allow publishing when run by MeshMonitor (off by default to avoid duplicate publishing). |

MQTT uses the `mosquitto_pub` command (package `mosquitto-clients`). Commands that arrive over an MQTT bridge (`VIA_MQTT=true`) never trigger a publish, which prevents bridge feedback loops. Note that `mosquitto_pub -P` puts the password on the local process command line. Use a dedicated low-privilege broker account.

---

## MeshMonitor-provided variables used

| Variable | Use |
|---|---|
| `MESSAGE` | Present → Auto Responder command mode |
| `TIMER_NAME` | Present (without `MESSAGE`) → Timer Trigger notification mode |
| `MM_LAT`, `MM_LON` | Location fallback |
| `TZ` | Time zone fallback |
| `VIA_MQTT` | MQTT loop prevention |
