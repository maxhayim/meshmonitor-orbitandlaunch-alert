# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Orbit and Launch Alert, please report it privately through [GitHub's private vulnerability reporting](https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert/security/advisories/new) or contact the maintainer directly.

Do not publish exploit details publicly before coordination.

## Supported Versions

| Version | Supported |
|---|---|
| v1.0.x | ✅ |

## RF Network Warning

Mesh traffic (Meshtastic, MeshCore, or any other mesh network MeshMonitor supports) may be observable depending on network configuration.

Do not transmit API keys, secrets, or sensitive information over public mesh channels.

Use Direct Messages when possible.

## Secure Development Practices

- No credentials are hardcoded. The optional `OLA_LL2_API_KEY` and `OLA_MQTT_PASSWORD` come only from the environment and are never included in mesh messages, MQTT events, logs, or status replies.
- `.env` files are git-ignored. `.env.example` contains no real secrets.
- Network access is limited to the configured Launch Library 2 and CelesTrak endpoints (and an optional MQTT broker). No scraping.
- All provider responses are schema-validated before use. Malformed data is dropped, not trusted.
- Cache and state files are written atomically and are size-bounded.
- In MeshMonitor mode stdout carries only the JSON response. Errors go to stderr, so tracebacks never reach the mesh.
- MQTT is off by default, off inside MeshMonitor unless explicitly allowed, and never republishes bridge traffic (`VIA_MQTT=true`).
- `mosquitto_pub -P` places the MQTT password on the local process command line. Use a dedicated, low-privilege broker account.
