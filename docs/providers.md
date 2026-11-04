# Data Providers

Orbit and Launch Alert uses two official, public data sources. Both are implemented in `orbit_and_launch_alert.py` and covered by offline tests with mocked responses.

| Data | Provider | Selector | Access |
|---|---|---|---|
| 🚀 Rocket launch schedules | The Space Devs, Launch Library 2 | `OLA_LAUNCH_PROVIDER=launchlibrary2` | Free, anonymous (rate limited); optional personal token |
| 🛰️ Orbital elements | CelesTrak GP data | `OLA_SATELLITE_PROVIDER=celestrak` | Free, no account |
| 🧮 Orbit propagation | Local, [`sgp4`](https://pypi.org/project/sgp4/) (MIT) | — | Runs on your machine |

---

## 🚀 The Space Devs: Launch Library 2

- Documentation: https://ll.thespacedevs.com/docs
- Base URL used: `https://ll.thespacedevs.com/2.3.0` (`OLA_LL2_BASE`)
- Development server: `https://lldev.thespacedevs.com/2.3.0` (relaxed limits, stale data, for testing only)

### Endpoints

| Purpose | Request |
|---|---|
| Upcoming launches | `GET /launches/upcoming/?limit=<OLA_LAUNCH_LIMIT>&mode=normal` |
| Recent launches (outcomes) | `GET /launches/previous/?limit=10&mode=normal` |

Fields used from each launch: `id`, `name`, `net`, `net_precision`, `status` (`id`, `name`, `abbrev`), `launch_service_provider` (`name`, `abbrev`), `rocket.configuration.name`, `mission` (`name`, `description`, `info_urls`), `pad` (`name`, `latitude`, `longitude`, `country`, `location.name`). The parser also accepts the older 2.2.0 shape (`location.country_code`). Records missing `id`, `name`, `net`, or `status` are skipped. A response without a `results` list is rejected.

### Launch statuses

| id | Status | How it is used |
|---|---|---|
| 1 | Go for Launch | `GO` in launch lines |
| 2 | To Be Determined | `TBD`; time shown as `NET …` |
| 8 | To Be Confirmed | `TBC` |
| 5 | On Hold | `⏸️ LAUNCH HOLD` alert |
| 6 | In Flight | `🚀 LIFTOFF CONFIRMED … IN FLIGHT (LL2)` alert |
| 3 | Launch Successful | `✅ LAUNCH SUCCESS` alert |
| 4 | Launch Failure | `❌ LAUNCH FAILURE` alert |
| 7 | Partial Failure | `⚠️ PARTIAL FAILURE` alert |

Outcomes are reported **only** from these statuses. Launch Library 2 does not currently define a "canceled" status. If one appears with "cancel" in its name, it is reported as `❌ LAUNCH CANCELED`. A launch that drops off the upcoming list before its NET is reported as `⚠️ LAUNCH REMOVED FROM SCHEDULE`, never as canceled. This only applies to launches inside the time window the fetched list covers, because a far-future launch can fall off the end of a limited page without being removed. Changing your filters never triggers it.

### Rate limits

Anonymous access is limited to a small number of requests per hour (see the Launch Library 2 documentation for the current figure; it has been 15 requests/hour). Orbit and Launch Alert:

- refreshes the upcoming list at most every `OLA_LAUNCH_REFRESH_MINUTES` (default 15 → 4 requests/hour),
- refreshes the previous list at most every 2 × that (default 30 → 2 requests/hour),
- answers commands from the cache in between,
- treats HTTP 429 and "throttled" responses as rate limiting, honors `Retry-After` (default 1 hour), and serves cached data meanwhile,
- uses exponential backoff (1, 2, 4 … up to 60 minutes) after other errors.

Running Orbit and Launch Alert standalone **and** inside MeshMonitor with separate cache directories doubles the request count. Share one `OLA_CACHE_DIR` or set a personal `OLA_LL2_API_KEY`.

---

## 🛰️ CelesTrak GP data

- Documentation: https://celestrak.org/NORAD/documentation/gp-data-formats.php
- Endpoint: `https://celestrak.org/NORAD/elements/gp.php` (`OLA_CELESTRAK_GP_URL`)
- Format: `FORMAT=json` (CCSDS OMM keywords). JSON is used instead of TLE because it supports catalog numbers above 99999.

| Purpose | Request |
|---|---|
| A group | `?GROUP=stations&FORMAT=json` |
| One object | `?CATNR=25544&FORMAT=json` |

Common groups: `stations` (ISS, Tiangong, visiting vehicles), `visual` (bright objects), `amateur` (amateur radio), `noaa`, `weather`, `starlink`, `active`. Any valid CelesTrak group name can be used.

Fields used: `OBJECT_NAME`, `OBJECT_ID`, `NORAD_CAT_ID`, `EPOCH`, `MEAN_MOTION`, `ECCENTRICITY`, `INCLINATION`, `RA_OF_ASC_NODE`, `ARG_OF_PERICENTER`, `MEAN_ANOMALY`, `BSTAR`, `MEAN_MOTION_DOT`, `MEAN_MOTION_DDOT`. Incomplete or non-numeric records are dropped. An unknown `CATNR` (CelesTrak answers `No GP data found`) is reported as "not found".

### Download etiquette

CelesTrak updates GP data about every 2 hours and blocks clients that repeatedly download the same data. Orbit and Launch Alert caches each group or catalog number for `OLA_GP_REFRESH_HOURS` (default 12, minimum 2) and treats HTTP 403 as a block (2-hour backoff). Large groups such as `starlink` are downloaded once and then capped to `OLA_MAX_SATELLITES` objects for prediction.

---

## 🧮 Local orbit prediction

- Propagator: [`sgp4`](https://github.com/brandon-rhodes/python-sgp4) by Brandon Rhodes (MIT License), the standard SGP4/SDP4 implementation (Vallado et al., 2020 revision). The compiled extension is used when available; otherwise its pure-Python mode is used automatically (about 0.02 s per satellite per day of prediction).
- Frames: SGP4 output (TEME) is rotated to Earth-fixed using IAU-82 Greenwich mean sidereal time (UT1 ≈ UTC, polar motion ignored). That's well under 0.01° for pass geometry.
- Passes: 30-second scan, bisection to about 1 s for AOS/LOS, ternary search for culmination. Cross-checked against [Skyfield](https://rhodesmill.org/skyfield/): AOS/TCA/LOS within 1 s, azimuth/elevation within 0.003°.
- Sun: low-precision solar ephemeris from the Astronomical Almanac (about 0.01°), used for observer darkness and satellite illumination.
- Shadow: cylindrical Earth-shadow model (penumbra ignored).

### Visible vs. geometric passes

| Label | Meaning |
|---|---|
| `🛰️ … ` | **Geometric overpass**: the satellite rises above your horizon and peaks at ≥ `OLA_MIN_ELEVATION_DEG`. Says nothing about whether you can see it. |
| `LIKELY VISIBLE` / `🌌 … VISIBLE` | **Visibility estimate**: at some point during the pass, the satellite is ≥ 10° high, your sun is ≤ −6° (darker than civil twilight), and the satellite is in sunlight. Weather, light pollution, terrain, and brightness are not modeled. |

When illumination was not evaluated (e.g. `!ola satellites`), no visibility claim is made at all.

### Element age

SGP4 accuracy decreases as elements age, especially for low orbits after reboosts. Elements older than `OLA_MAX_ELEMENT_AGE_DAYS` (default 7) are refused with `⚠️ … ELEMENTS EXPIRED`. Cached element sets are otherwise usable offline for up to 30 days of cache age.

---

## Amateur radio satellites

Use `OLA_SATELLITE_GROUPS=amateur` or specific NORAD IDs. Responses include name, NORAD ID, AOS, LOS, maximum elevation, and duration.

**No frequencies are included.** CelesTrak GP data has no frequency information, and Orbit and Launch Alert does not invent it. A future frequency feature would need a verified source with clear licensing (e.g. SatNOGS DB, CC BY-SA).

---

## Not directly supported (documented only)

| Service | Why not |
|---|---|
| Next Spaceflight | Website only; would require scraping |
| RocketLaunch.live | Separate API with its own key and terms; may be added later as an optional provider |
| N2YO, Heavens-Above | Hosted pass-prediction services; the design goal is local prediction |
| Space-Track.org | Requires an account; CelesTrak redistributes the same public GP data |

Orbit and Launch Alert does not scrape websites or bypass terms of service.
