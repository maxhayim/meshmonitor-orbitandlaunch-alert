# Examples

All output below was produced by `orbit_and_launch_alert.py` using the **synthetic test fixtures** in `tests/fixtures/` (invented "Test …" launches, plus the published 2008 ISS example element set) with `OLA_TIMEZONE=America/New_York` and a Miami location. They show formats only, not real events.

---

## Auto Responder commands

```text
!ola              → {"response": "🚀 FALCON 9 | Test Starlink Group 99-1 | CCSFS | 08:00 EST | T-1H | GO | 193MI AWAY"}
!ola launch       → {"response": "🚀 FALCON 9 | Test Starlink Group 99-1 | CCSFS | 08:00 EST | T-1H | GO | 193MI AWAY"}
!ola launches     → {"response": "🚀 3 in 24h: FALCON 9 08:00 EST CCSFS | VULCAN VC4S 15:15 EST CCSFS | ELECTRON 21:30 EST MAHIA NZ"}
!ola iss          → {"response": "🛰️ ISS | 18:45 EDT | MAX 40° | 10MIN | SW→NE"}
!ola satellites   → {"response": "🛰️ ISS 18:45 EDT 40° | ISS Sun 02:50 EDT 11° | ISS Sun 04:24 EDT 31°"}
!ola sat 25544    → {"response": "🛰️ ISS | 18:45 EDT | MAX 40° | 10MIN | SW→NE"}
!ola visible      → {"response": "🌌 No predicted visible passes in 24h"}
!ola status       → {"response": "🟢 OLA v1.0.0 | mode launches | LL2 0M ago | GP no cache | sgp4 ok | loc ok | ledger 0"}
!ola help         → {"response": "!ola [launch|launches|iss|satellites|sat <NORAD>|visible|status|demo|help]"}
!ola sat abc      → {"response": "⚠️ Invalid NORAD ID 'abc'"}
!ola rocketz      → {"response": "❓ Unknown 'rocketz' | try !ola help"}
```

Times in user-facing messages are always labeled with the time zone (`EST`/`EDT`, `UTC`, …). A day name is added when the event is not today (`Sun 02:50 EDT`). TBD launches show `NET` ("no earlier than").

## Timer Trigger output

First run with the fixture schedule (T-1H, plus T-24H alerts for later launches and two recent outcomes):

```json
{"responses": [
  "🚀 FALCON 9 | CCSFS | T-1H | Test Starlink Group 99-1 | 08:00 EST | GO | 193MI AWAY",
  "🚀 VULCAN VC4S | CCSFS | T-8H15M | Test USSF-999 | 15:15 EST | GO | 195MI AWAY",
  "🚀 ELECTRON | MAHIA NZ | T-14H30M | Test Payload | 21:30 EST | TBC | 7931MI AWAY",
  "❌ LAUNCH FAILURE | LONG MARCH 2D | Test Previous Failure | TAIYUAN",
  "✅ LAUNCH SUCCESS | ELECTRON | Test Previous Success | MAHIA NZ"
]}
```

Second run five minutes later (nothing new; MeshMonitor sends nothing):

```json
{"response": ""}
```

## Other alert types

Generated the same way. The `CACHED` and `EXPIRED` lines show how stale or expired data is labeled.

```text
⚠️ LAUNCH DELAYED | FALCON 9 | Test Starlink Group 99-1 | NEW Fri 07:00 EST | T-23H50M
🔁 LAUNCH RESCHEDULED | STARSHIP | Integrated Flight Test… | NEW Thu 07:00 EST | T-14D
⏸️ LAUNCH HOLD | FALCON 9 | Test Starlink Group 99-1 | CCSFS
🕒 SCHEDULED T-0 | FALCON 9 | Test Starlink Group 99-1 | CCSFS | LIFTOFF NOT YET CONFIRMED
🚀 LIFTOFF CONFIRMED | FALCON 9 | Test Starlink Group 99-1 | IN FLIGHT (LL2)
⚠️ LAUNCH REMOVED FROM SCHEDULE | VULCAN VC4S
🌌 ISS VISIBLE | 20:32 EDT | MAX 50° | 10MIN | NW→SE | EST. NOT GUARANTEED
🛰️ ISS | 20:30 EDT | MAX 50° | 10MIN | NW→SE | LIKELY VISIBLE
🛰️ ISS | 22:45 UTC | MAX 40° | 10MIN | SW→NE | CACHED 3H
⚠️ ISS ELEMENTS EXPIRED (19D) | refresh needed
```

## Demo mode

```bash
OLA_MODE=demo python3 orbit_and_launch_alert.py
```

```text
🟢 Orbit and Launch Alert demo started (SIMULATED data, not live)
🧪 DEMO 🚀 FALCON 9 | KSC | T-1H
🧪 DEMO 🚀 STARSHIP | T-15M | TX
🧪 DEMO ⚠️ LAUNCH DELAYED | FALCON 9
🧪 DEMO ✅ LAUNCH SUCCESS | ELECTRON
🧪 DEMO 🛰️ ISS | 17:00 UTC | MAX 65° | 6MIN
🧪 DEMO 🛰️ NOAA 19 | AOS 14:35 UTC | 48°
🧪 DEMO 🌌 ISS VISIBLE | 17:00 UTC | 6MIN
🧪 DEMO 🚀 No launches in next 24h
✅ Demo complete
```

Demo times are relative to the current clock. Every line is tagged `🧪 DEMO`.

## Recipes

**Florida launches only, with 2-hour and 10-minute warnings:**

```bash
OLA_MODE=launches
OLA_LAUNCH_SITES=cape
OLA_LAUNCH_ALERT_HOURS=2
OLA_LAUNCH_ALERT_MINUTES=10,0
```

**Amateur satellites above 20°, plus the ISS:**

```bash
OLA_MODE=satellites
OLA_SATELLITE_GROUPS=amateur
OLA_SATELLITE_IDS=25544
OLA_MIN_ELEVATION_DEG=20
```

**Only announce ISS passes predicted to be visible:**

```bash
OLA_MODE=iss
OLA_ISS_VISIBLE_ONLY=true
OLA_PASS_ALERT_MINUTES=15
```

**Standalone with MQTT over Tailscale:**

```bash
OLA_MODE=orbit_and_launch
OLA_MQTT_HOST=mqtt-broker.tailnet-name.ts.net
OLA_MQTT_TOPIC=orbit-and-launch-alert/events
python3 orbit_and_launch_alert.py
```
