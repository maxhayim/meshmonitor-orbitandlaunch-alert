# Changelog

All notable changes to Orbit and Launch Alert are documented here.
This project uses [Semantic Versioning](https://semver.org/) with its own version sequence.

## [v1.0.0] — 2026-11-04

Initial release: rocket launches and satellite tracking for MeshMonitor.

### Added
- `orbit_and_launch_alert.py`, a single-file MeshMonitor script (Python 3.8+) with `mm_meta` (name, emoji, version, author, source).
- Rocket launches from The Space Devs Launch Library 2 (`2.3.0`): countdown alerts (default T-24H, T-1H, T-15M, scheduled T-0), delay / reschedule / hold / cancellation / removed-from-schedule alerts, and outcomes (success, failure, partial failure, in flight) only when the source reports them.
- Launch filters: country, agency, launch site (with `cape`, `vandenberg`, `starbase` presets), rocket, and mission. Optional distance to the pad.
- Satellite passes from CelesTrak GP (OMM JSON) propagated locally with `sgp4`: AOS, max elevation and time, LOS, duration, azimuths. Groups and NORAD IDs supported.
- ISS visible-pass estimates (dark sky, sunlit satellite, elevation), labeled as estimates.
- Commands: `!ola`, `!ola launch`, `!ola launches`, `!ola iss`, `!ola satellites`, `!ola sat <NORAD>`, `!ola visible`, `!ola status`, `!ola demo`, `!ola help` (plus `/ola`).
- MeshMonitor Auto Responder mode (`MESSAGE`) and Timer Trigger mode (`TIMER_NAME`) with JSON-only stdout.
- Standalone polling mode with `--once`.
- Persistent notification ledger, atomic bounded cache, freshness labels (`CACHED 3H`), offline operation from valid cache.
- Rate-limit handling: HTTP 429 / throttle detection, `Retry-After`, exponential backoff, CelesTrak 403 handling.
- Optional MQTT publishing via `mosquitto_pub`, off by default, off inside MeshMonitor unless allowed, never republishes MQTT-bridged commands.
- Offline demo mode with clearly labeled simulated alerts.
- Offline test suite with synthetic fixtures; GitHub Actions on Python 3.8 and 3.12.
- Documentation: README, configuration, providers, MeshMonitor setup, examples.
