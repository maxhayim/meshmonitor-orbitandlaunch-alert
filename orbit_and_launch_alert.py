#!/usr/bin/env python3
# mm_meta:
#   name: Orbit and Launch Alert
#   emoji: 🚀
#   language: Python
#   version: 1.0.0
#   author: maxhayim
#   source: maxhayim/meshmonitor-orbitandlaunch-alert/orbit_and_launch_alert.py
"""
Orbit and Launch Alert — v1.0.0

A lightweight MeshMonitor script providing emoji-based rocket launch
notifications, satellite overpass predictions, and ISS visibility alerts over
Meshtastic and MeshCore networks using official launch schedules and orbital
data.

Companion to Sky and Sea Alert (maxhayim/meshmonitor-skyandsea-alert).

Data providers:
- Rocket launches: The Space Devs Launch Library 2 (LL2) API, v2.3.0
- Orbital elements: CelesTrak GP data (OMM JSON), propagated locally with the
  MIT-licensed `sgp4` package (optional dependency; launches work without it)

Run modes:
- MeshMonitor Auto Responder: if the MESSAGE env var exists, the script parses
  one `!ola` command and prints exactly one JSON object {"response": "..."}.
- MeshMonitor Timer Trigger: if TIMER_NAME exists (and MESSAGE does not), the
  script runs one notification cycle and prints {"response": ""} (nothing due)
  or {"responses": [...]} (new alerts). The persistent ledger prevents repeats.
- Standalone: polls on an interval and prints alerts to stdout, with optional
  MQTT publish via mosquitto_pub (disabled by default). `--once` runs one cycle.

No scraping. No shared keys. No direct radio transmission.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Dict, List, Optional, Tuple

OLA_VERSION = "v1.0.0"
OLA_SOURCE = "orbit-and-launch-alert"
OLA_REPO_URL = "https://github.com/maxhayim/meshmonitor-orbitandlaunch-alert"
USER_AGENT = f"{OLA_SOURCE}/{OLA_VERSION} (+{OLA_REPO_URL})"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Optional vendor directory (e.g. `pip install --target <dir> sgp4`) so the
# script works inside the MeshMonitor container without touching its venv.
_VENDOR_DIR = os.getenv("OLA_VENDOR_DIR") or os.path.join(SCRIPT_DIR, "ola_vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

try:  # pragma: no cover - import guard
    from sgp4.api import Satrec, WGS72  # type: ignore
    from sgp4 import omm as _sgp4_omm  # type: ignore
    HAVE_SGP4 = True
except Exception:  # pragma: no cover - exercised when sgp4 is absent
    Satrec = None  # type: ignore
    WGS72 = None  # type: ignore
    _sgp4_omm = None  # type: ignore
    HAVE_SGP4 = False

try:
    from zoneinfo import ZoneInfo  # type: ignore  # Python 3.9+
except Exception:  # pragma: no cover - Python 3.8
    try:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    except Exception:
        ZoneInfo = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODES = ("demo", "launches", "satellites", "iss", "orbit_and_launch")
MODE_ALIASES = {
    "orbit+launch": "orbit_and_launch",
    "orbit-and-launch": "orbit_and_launch",
    "all": "orbit_and_launch",
    "launch": "launches",
    "sats": "satellites",
    "satellite": "satellites",
}

LAUNCH_PROVIDERS = ("launchlibrary2",)
SATELLITE_PROVIDERS = ("celestrak",)

DEFAULT_LL2_BASE = "https://ll.thespacedevs.com/2.3.0"
DEFAULT_CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"

ISS_NORAD = 25544
TIANGONG_NORAD = 48274

# Upper bound for one provider response (the full CelesTrak starlink group is ~6 MB).
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# Cached OMM records kept per group (predictions use OLA_MAX_SATELLITES of them).
GP_GROUP_CACHE_MAX = 500

# MeshMonitor truncates script responses to 200 characters.
MESHMONITOR_MAX_LEN = 200
MIN_MAX_LEN = 60

# LL2 status ids (https://ll.thespacedevs.com/2.3.0/config/launch_statuses/)
LL2_GO, LL2_TBD, LL2_SUCCESS, LL2_FAILURE = 1, 2, 3, 4
LL2_HOLD, LL2_IN_FLIGHT, LL2_PARTIAL, LL2_TBC = 5, 6, 7, 8
LL2_OUTCOME_IDS = (LL2_SUCCESS, LL2_FAILURE, LL2_PARTIAL)

EARTH_RADIUS_KM = 6378.137
WGS84_F = 1.0 / 298.257223563
AU_KM = 149597870.7

# Outcomes older than this are not announced (avoids stale bursts on first run).
OUTCOME_WINDOW_H = 12
# Timer Trigger: at most this many messages per run (rest summarised).
MAX_TIMER_MESSAGES = 5

# Sun altitude at or below which the observer sky is dark enough for a
# sunlit satellite to be seen (end of civil twilight).
DARK_SUN_ALT_DEG = -6.0
# Visible passes additionally need the satellite at least this high.
VISIBLE_MIN_EL_DEG = 10.0

COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Short names for well-known launch sites (fallback: first part of LL2 name).
SITE_ABBREVIATIONS = (
    ("kennedy space center", "KSC"),
    ("cape canaveral", "CCSFS"),
    ("vandenberg", "VSFB"),
    ("starbase", "STARBASE TX"),
    ("boca chica", "STARBASE TX"),
    ("wallops", "WALLOPS"),
    ("corn ranch", "VAN HORN TX"),
    ("mahia", "MAHIA NZ"),
    ("rocket lab launch complex", "MAHIA NZ"),
    ("guiana space centre", "KOUROU"),
    ("kourou", "KOUROU"),
    ("baikonur", "BAIKONUR"),
    ("plesetsk", "PLESETSK"),
    ("vostochny", "VOSTOCHNY"),
    ("tanegashima", "TANEGASHIMA"),
    ("uchinoura", "UCHINOURA"),
    ("satish dhawan", "SRIHARIKOTA"),
    ("sriharikota", "SRIHARIKOTA"),
    ("jiuquan", "JIUQUAN"),
    ("xichang", "XICHANG"),
    ("taiyuan", "TAIYUAN"),
    ("wenchang", "WENCHANG"),
    ("haiyang", "HAIYANG"),
    ("kodiak", "KODIAK"),
    ("naro", "NARO"),
)

# Site presets usable in OLA_LAUNCH_SITES.
SITE_PRESETS = {
    "cape": ("cape canaveral", "kennedy space center"),
    "florida": ("cape canaveral", "kennedy space center"),
    "ksc": ("kennedy space center",),
    "ccsfs": ("cape canaveral",),
    "vandenberg": ("vandenberg",),
    "starbase": ("starbase", "boca chica"),
}

GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
NORAD_RE = re.compile(r"^[0-9]{1,9}$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Format an aware datetime as ISO 8601 UTC with a trailing Z."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp into an aware UTC datetime (None if invalid)."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Python < 3.11 rejects fractional seconds that are not 3 or 6 digits.
        m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
        if not m:
            return None
        frac = (m.group(2) or ".0")[1:7].ljust(6, "0")
        try:
            dt = datetime.fromisoformat(f"{m.group(1)}.{frac}{m.group(3)}")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _log(msg: str) -> None:
    """Diagnostics always go to stderr so stdout stays JSON-only in MeshMonitor."""
    if os.getenv("OLA_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        print(f"[ola] {msg}", file=sys.stderr, flush=True)


def _status(msg: str) -> None:
    """Standalone status line (stdout), mirroring Sky and Sea Alert."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{ts} {msg}".strip(), flush=True)


def clamp_text(s: str, max_len: int = MESHMONITOR_MAX_LEN) -> str:
    """Trim a message to max_len characters, ending with an ellipsis if cut."""
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip(" |") + "…"


def fit_parts(parts: List[str], max_len: int, sep: str = " | ") -> str:
    """Join message parts, dropping trailing optional parts before hard-clamping.

    Parts are ordered most-important first. Empty parts are skipped.
    """
    parts = [p.strip() for p in parts if p and p.strip()]
    while len(parts) > 2 and len(sep.join(parts)) > max_len:
        parts.pop()
    return clamp_text(sep.join(parts), max_len)


def compass(az_deg: float) -> str:
    """Convert an azimuth in degrees to an 8-point compass label."""
    return COMPASS[int(((az_deg % 360.0) + 22.5) // 45.0) % 8]


def fmt_countdown(delta: timedelta) -> str:
    """Format a time-until as T-1D3H / T-5H12M / T-15M / T-0."""
    secs = int(round(delta.total_seconds()))
    if secs <= 0:
        return "T-0"
    mins = (secs + 59) // 60
    d, rem = divmod(mins, 1440)
    h, m = divmod(rem, 60)
    if d:
        return f"T-{d}D{h}H" if h else f"T-{d}D"
    if h:
        return f"T-{h}H{m}M" if m else f"T-{h}H"
    return f"T-{m}M"


def fmt_threshold(minutes: int) -> str:
    """Format an alert threshold in minutes as T-24H / T-1H / T-15M / T-0."""
    if minutes <= 0:
        return "T-0"
    if minutes % 60 == 0:
        return f"T-{minutes // 60}H"
    return f"T-{minutes}M"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ConfigError(ValueError):
    """Raised for invalid configuration values."""


def _split_csv(value: str) -> List[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _parse_int_list(name: str, value: str, lo: int, hi: int) -> List[int]:
    out: List[int] = []
    for p in _split_csv(value):
        if not re.fullmatch(r"-?\d+", p):
            raise ConfigError(f"{name}: '{p}' is not a whole number")
        n = int(p)
        if n < lo or n > hi:
            raise ConfigError(f"{name}: {n} out of range {lo}..{hi}")
        out.append(n)
    return out


def parse_norad_id(value: Any) -> int:
    """Validate a NORAD catalog number (positive integer, up to 9 digits)."""
    s = str(value).strip()
    if not NORAD_RE.fullmatch(s) or int(s) <= 0:
        raise ConfigError(f"invalid NORAD ID '{s}'")
    return int(s)


def resolve_timezone(name: str) -> tzinfo:
    """Resolve an IANA timezone name. Raises ConfigError if unknown."""
    name = (name or "").strip()
    if name.upper() in ("UTC", "Z", "ETC/UTC", "GMT"):
        return timezone.utc
    if ZoneInfo is None:
        raise ConfigError(
            f"timezone '{name}' needs Python 3.9+ (zoneinfo) or backports.zoneinfo; set OLA_TIMEZONE=UTC"
        )
    if not re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*", name):
        raise ConfigError(f"invalid OLA_TIMEZONE '{name}'")
    try:
        return ZoneInfo(name)
    except Exception:
        raise ConfigError(f"unknown OLA_TIMEZONE '{name}'")


@dataclass
class Config:
    """All runtime settings, loaded from OLA_* environment variables."""

    mode: str = "demo"
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt_m: float = 0.0
    location_source: str = "unset"
    tz_name: str = "UTC"
    tz: tzinfo = timezone.utc
    units: str = "mi"
    max_len: int = MESHMONITOR_MAX_LEN

    launch_provider: str = "launchlibrary2"
    ll2_base: str = DEFAULT_LL2_BASE
    ll2_api_key: str = ""
    launch_limit: int = 25
    launch_refresh_min: int = 15
    launch_max_age_h: int = 24
    alert_thresholds_min: List[int] = field(default_factory=lambda: [1440, 60, 15, 0])
    alert_delays: bool = True
    alert_holds: bool = True
    alert_cancellations: bool = True
    alert_outcomes: bool = True
    alert_in_flight: bool = True
    t0_grace_min: int = 30
    launch_countries: List[str] = field(default_factory=list)
    launch_agencies: List[str] = field(default_factory=list)
    launch_sites: List[str] = field(default_factory=list)
    launch_rockets: List[str] = field(default_factory=list)
    launch_missions: List[str] = field(default_factory=list)
    show_distance: bool = True

    satellite_provider: str = "celestrak"
    celestrak_gp_url: str = DEFAULT_CELESTRAK_GP_URL
    satellite_groups: List[str] = field(default_factory=lambda: ["stations"])
    satellite_ids: List[int] = field(default_factory=list)
    min_elevation_deg: float = 30.0
    lookahead_hours: int = 24
    gp_refresh_hours: int = 12
    max_element_age_days: float = 7.0
    max_satellites: int = 20
    pass_alert_minutes: int = 10
    alert_iss_visible_only: bool = False
    compute_budget_s: float = 15.0

    poll_interval: int = 300
    suppress_minutes: int = 15
    cache_dir: str = os.path.join(SCRIPT_DIR, "cache")
    http_timeout: int = 10

    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_topic: str = "orbit-and-launch-alert/events"
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    mqtt_allow_in_meshmonitor: bool = False

    errors: List[str] = field(default_factory=list)

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


def load_config(env: Optional[Dict[str, str]] = None) -> Config:
    """Build a Config from the environment. Invalid values are collected in
    cfg.errors (never silently replaced by defaults)."""
    env = dict(os.environ if env is None else env)
    cfg = Config()
    errors: List[str] = []

    def get(name: str, default: str = "") -> str:
        v = env.get(name)
        return default if v is None else v.strip()

    def get_bool(name: str, default: bool) -> bool:
        v = get(name).lower()
        if not v:
            return default
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
        errors.append(f"{name}: '{v}' is not true/false")
        return default

    def get_int(name: str, default: int, lo: int, hi: int) -> int:
        v = get(name)
        if not v:
            return default
        if not re.fullmatch(r"-?\d+", v):
            errors.append(f"{name}: '{v}' is not a whole number")
            return default
        n = int(v)
        if n < lo or n > hi:
            errors.append(f"{name}: {n} out of range {lo}..{hi}")
            return default
        return n

    def get_float(name: str, default: float, lo: float, hi: float) -> float:
        v = get(name)
        if not v:
            return default
        try:
            x = float(v)
        except ValueError:
            errors.append(f"{name}: '{v}' is not a number")
            return default
        if not math.isfinite(x) or x < lo or x > hi:
            errors.append(f"{name}: {v} out of range {lo:g}..{hi:g}")
            return default
        return x

    # Mode
    mode = get("OLA_MODE", "demo").lower()
    mode = MODE_ALIASES.get(mode, mode)
    if mode not in MODES:
        errors.append(f"OLA_MODE: '{mode}' (use {'|'.join(MODES)})")
        mode = "demo"
    cfg.mode = mode

    # Location: OLA_LAT/OLA_LON, else MeshMonitor node position (MM_LAT/MM_LON).
    lat_s, lon_s = get("OLA_LAT"), get("OLA_LON")
    if lat_s or lon_s:
        if not (lat_s and lon_s):
            errors.append("OLA_LAT and OLA_LON must both be set")
        else:
            lat = get_float("OLA_LAT", float("nan"), -90.0, 90.0)
            lon = get_float("OLA_LON", float("nan"), -180.0, 180.0)
            if math.isfinite(lat) and math.isfinite(lon):
                cfg.lat, cfg.lon, cfg.location_source = lat, lon, "OLA_LAT/OLA_LON"
    else:
        mm_lat, mm_lon = get("MM_LAT"), get("MM_LON")
        try:
            la, lo = float(mm_lat), float(mm_lon)
            if -90 <= la <= 90 and -180 <= lo <= 180 and (la, lo) != (0.0, 0.0):
                cfg.lat, cfg.lon, cfg.location_source = la, lo, "MeshMonitor node"
        except ValueError:
            pass
    cfg.alt_m = get_float("OLA_ALT_M", 0.0, -500.0, 9000.0)

    # Timezone: OLA_TIMEZONE, else TZ (set by MeshMonitor), else UTC.
    tz_name = get("OLA_TIMEZONE") or get("TZ") or "UTC"
    if tz_name.startswith(":"):
        tz_name = tz_name[1:]
    try:
        cfg.tz = resolve_timezone(tz_name)
        cfg.tz_name = tz_name
    except ConfigError as e:
        errors.append(str(e))

    units = get("OLA_UNITS", "mi").lower()
    if units not in ("mi", "km"):
        errors.append(f"OLA_UNITS: '{units}' (use mi|km)")
    else:
        cfg.units = units
    cfg.max_len = get_int("OLA_MAX_LEN", MESHMONITOR_MAX_LEN, MIN_MAX_LEN, MESHMONITOR_MAX_LEN)

    # Launches
    lp = get("OLA_LAUNCH_PROVIDER", "launchlibrary2").lower()
    if lp in ("ll2", "thespacedevs", "launch_library_2"):
        lp = "launchlibrary2"
    if lp not in LAUNCH_PROVIDERS:
        errors.append(f"OLA_LAUNCH_PROVIDER: '{lp}' (use launchlibrary2)")
    cfg.launch_provider = "launchlibrary2"
    cfg.ll2_base = get("OLA_LL2_BASE", DEFAULT_LL2_BASE).rstrip("/")
    if not cfg.ll2_base.startswith(("https://", "http://")):
        errors.append("OLA_LL2_BASE must be an http(s) URL")
    cfg.ll2_api_key = get("OLA_LL2_API_KEY")
    cfg.launch_limit = get_int("OLA_LAUNCH_LIMIT", 25, 1, 100)
    cfg.launch_refresh_min = get_int("OLA_LAUNCH_REFRESH_MINUTES", 15, 5, 1440)
    cfg.launch_max_age_h = get_int("OLA_LAUNCH_MAX_AGE_HOURS", 24, 1, 168)
    try:
        hours = _parse_int_list("OLA_LAUNCH_ALERT_HOURS", get("OLA_LAUNCH_ALERT_HOURS", "24,1"), 1, 168)
        mins = _parse_int_list("OLA_LAUNCH_ALERT_MINUTES", get("OLA_LAUNCH_ALERT_MINUTES", "15,0"), 0, 1440)
        cfg.alert_thresholds_min = sorted({h * 60 for h in hours} | set(mins), reverse=True)
    except ConfigError as e:
        errors.append(str(e))
    cfg.alert_delays = get_bool("OLA_ALERT_DELAYS", True)
    cfg.alert_holds = get_bool("OLA_ALERT_HOLDS", True)
    cfg.alert_cancellations = get_bool("OLA_ALERT_CANCELLATIONS", True)
    cfg.alert_outcomes = get_bool("OLA_ALERT_OUTCOMES", True)
    cfg.alert_in_flight = get_bool("OLA_ALERT_IN_FLIGHT", True)
    cfg.launch_countries = [c.upper() for c in _split_csv(get("OLA_LAUNCH_COUNTRIES"))]
    for c in cfg.launch_countries:
        if not re.fullmatch(r"[A-Z]{2,3}", c):
            errors.append(f"OLA_LAUNCH_COUNTRIES: '{c}' is not an ISO country code")
    cfg.launch_agencies = [a.lower() for a in _split_csv(get("OLA_LAUNCH_AGENCIES"))]
    cfg.launch_sites = [s.lower() for s in _split_csv(get("OLA_LAUNCH_SITES"))]
    cfg.launch_rockets = [r.lower() for r in _split_csv(get("OLA_LAUNCH_ROCKETS"))]
    cfg.launch_missions = [m.lower() for m in _split_csv(get("OLA_LAUNCH_MISSIONS"))]
    cfg.show_distance = get_bool("OLA_SHOW_DISTANCE", True)

    # Satellites
    sp = get("OLA_SATELLITE_PROVIDER", "celestrak").lower()
    if sp not in SATELLITE_PROVIDERS:
        errors.append(f"OLA_SATELLITE_PROVIDER: '{sp}' (use celestrak)")
    cfg.celestrak_gp_url = get("OLA_CELESTRAK_GP_URL", DEFAULT_CELESTRAK_GP_URL)
    if not cfg.celestrak_gp_url.startswith(("https://", "http://")):
        errors.append("OLA_CELESTRAK_GP_URL must be an http(s) URL")
    groups = [g.lower() for g in _split_csv(get("OLA_SATELLITE_GROUPS", "stations"))]
    for g in groups:
        if not GROUP_RE.fullmatch(g):
            errors.append(f"OLA_SATELLITE_GROUPS: invalid group '{g}'")
    cfg.satellite_groups = [g for g in groups if GROUP_RE.fullmatch(g)]
    for s in _split_csv(get("OLA_SATELLITE_IDS")):
        try:
            cfg.satellite_ids.append(parse_norad_id(s))
        except ConfigError as e:
            errors.append(f"OLA_SATELLITE_IDS: {e}")
    cfg.min_elevation_deg = get_float("OLA_MIN_ELEVATION_DEG", 30.0, 0.0, 89.0)
    cfg.lookahead_hours = get_int("OLA_LOOKAHEAD_HOURS", 24, 1, 72)
    cfg.gp_refresh_hours = get_int("OLA_GP_REFRESH_HOURS", 12, 2, 168)
    cfg.max_element_age_days = get_float("OLA_MAX_ELEMENT_AGE_DAYS", 7.0, 0.5, 30.0)
    cfg.max_satellites = get_int("OLA_MAX_SATELLITES", 20, 1, 200)
    cfg.pass_alert_minutes = get_int("OLA_PASS_ALERT_MINUTES", 10, 0, 720)
    cfg.alert_iss_visible_only = get_bool("OLA_ISS_VISIBLE_ONLY", False)
    cfg.compute_budget_s = get_float("OLA_COMPUTE_BUDGET_SECONDS", 15.0, 1.0, 600.0)

    # Runtime
    cfg.poll_interval = get_int("OLA_POLL_INTERVAL", 300, 60, 86400)
    cfg.suppress_minutes = get_int("OLA_SUPPRESS_MINUTES", 15, 0, 1440)
    cache_dir = get("OLA_CACHE_DIR", "./cache") or "./cache"
    if not os.path.isabs(cache_dir):
        cache_dir = os.path.normpath(os.path.join(SCRIPT_DIR, cache_dir))
    cfg.cache_dir = cache_dir
    cfg.http_timeout = get_int("OLA_HTTP_TIMEOUT", 10, 1, 60)

    # MQTT
    cfg.mqtt_host = get("OLA_MQTT_HOST")
    cfg.mqtt_port = get_int("OLA_MQTT_PORT", 1883, 1, 65535)
    cfg.mqtt_topic = get("OLA_MQTT_TOPIC", "orbit-and-launch-alert/events")
    if any(ch in cfg.mqtt_topic for ch in "#+") or not cfg.mqtt_topic:
        errors.append("OLA_MQTT_TOPIC must be a non-empty topic without wildcards")
    cfg.mqtt_username = get("OLA_MQTT_USERNAME")
    cfg.mqtt_password = get("OLA_MQTT_PASSWORD")
    cfg.mqtt_tls = get_bool("OLA_MQTT_TLS", False)
    cfg.mqtt_allow_in_meshmonitor = get_bool("OLA_MQTT_ALLOW_IN_MESHMONITOR", False)

    cfg.errors = errors
    return cfg


# ---------------------------------------------------------------------------
# Cache, state and HTTP
# ---------------------------------------------------------------------------

class ProviderError(RuntimeError):
    """A data provider failed (network, HTTP status, or bad schema)."""

    def __init__(self, msg: str, code: Optional[int] = None):
        super().__init__(msg)
        self.code = code


class RateLimited(ProviderError):
    """Provider answered 429 / throttled."""

    def __init__(self, msg: str, retry_after_s: Optional[int] = None):
        super().__init__(msg, 429)
        self.retry_after_s = retry_after_s


def atomic_write_json(path: str, data: Any) -> None:
    """Write JSON atomically (temp file in the same directory + os.replace)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str) -> Optional[Any]:
    """Read JSON from disk; None if missing or corrupt."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def http_get(url: str, timeout: int, headers: Optional[Dict[str, str]] = None) -> Tuple[int, bytes]:
    """GET a URL. Returns (status, body). Raises RateLimited / ProviderError."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ra = e.headers.get("Retry-After") if e.headers else None
            secs = int(ra) if ra and ra.strip().isdigit() else None
            raise RateLimited("HTTP 429 rate limited", secs)
        raise ProviderError(f"HTTP {e.code}", e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ProviderError(f"network error: {getattr(e, 'reason', e)}")


class Store:
    """Persistent cache + state (notification ledger, launch history, backoff)."""

    LEDGER_MAX = 2000
    LEDGER_TTL_S = 14 * 86400
    SEEN_MAX = 500
    GP_FILES_MAX = 24

    def __init__(self, cache_dir: str):
        self.dir = cache_dir
        self.state_path = os.path.join(cache_dir, "state.json")
        st = read_json(self.state_path)
        if not isinstance(st, dict):
            st = {}
        self.ledger: Dict[str, Dict[str, Any]] = st.get("ledger") if isinstance(st.get("ledger"), dict) else {}
        self.seen: Dict[str, Dict[str, Any]] = st.get("seen") if isinstance(st.get("seen"), dict) else {}
        self.backoff: Dict[str, Dict[str, Any]] = st.get("backoff") if isinstance(st.get("backoff"), dict) else {}
        self.suppress: Dict[str, float] = st.get("suppress") if isinstance(st.get("suppress"), dict) else {}

    # -- cache files -------------------------------------------------------

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def load(self, name: str) -> Optional[Dict[str, Any]]:
        data = read_json(self.path(name))
        if not isinstance(data, dict) or "fetched" not in data or "data" not in data:
            return None
        if _parse_iso(data.get("fetched")) is None:
            return None
        return data

    def save(self, name: str, payload: Any, source: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        rec = {"fetched": _iso(now or _utcnow()), "source": source, "version": OLA_VERSION, "data": payload}
        atomic_write_json(self.path(name), rec)
        if name.startswith("gp_"):
            self._prune_gp_files()
        return rec

    def _prune_gp_files(self) -> None:
        try:
            files = [f for f in os.listdir(self.dir) if f.startswith("gp_") and f.endswith(".json")]
        except OSError:
            return
        if len(files) <= self.GP_FILES_MAX:
            return
        files.sort(key=lambda f: os.path.getmtime(self.path(f)))
        for f in files[: len(files) - self.GP_FILES_MAX]:
            try:
                os.unlink(self.path(f))
            except OSError:
                pass

    # -- backoff -------------------------------------------------------------

    def in_backoff(self, provider: str, now: float) -> bool:
        b = self.backoff.get(provider) or {}
        return float(b.get("until", 0)) > now

    def record_failure(self, provider: str, now: float, retry_after_s: Optional[int] = None) -> None:
        b = self.backoff.get(provider) or {}
        n = int(b.get("failures", 0)) + 1
        delay = retry_after_s if retry_after_s else min(3600, 60 * (2 ** (n - 1)))
        self.backoff[provider] = {"failures": n, "until": now + delay}

    def record_success(self, provider: str) -> None:
        self.backoff.pop(provider, None)

    # -- ledger --------------------------------------------------------------

    def was_sent(self, key: str) -> bool:
        return key in self.ledger

    def mark_sent(self, key: str, now: float, **extra: Any) -> None:
        rec = {"ts": now}
        rec.update(extra)
        self.ledger[key] = rec

    def forget(self, prefix: str, keep: Optional[Callable[[str, Dict[str, Any]], bool]] = None) -> None:
        for k in [k for k in self.ledger if k.startswith(prefix)]:
            if keep is None or not keep(k, self.ledger[k]):
                del self.ledger[k]

    def suppressed(self, text: str, now: float, minutes: int) -> bool:
        """Short repeat-suppression window for identical alert text."""
        window = max(0, minutes) * 60
        for k in [k for k, t in self.suppress.items() if now - float(t) >= max(window, 1)]:
            del self.suppress[k]
        if window and text in self.suppress:
            return True
        self.suppress[text] = now
        return False

    def prune(self, now: float) -> None:
        for k in [k for k, v in self.ledger.items() if now - float(v.get("ts", 0)) > self.LEDGER_TTL_S]:
            del self.ledger[k]
        if len(self.ledger) > self.LEDGER_MAX:
            for k in sorted(self.ledger, key=lambda k: float(self.ledger[k].get("ts", 0)))[: len(self.ledger) - self.LEDGER_MAX]:
                del self.ledger[k]
        for k in [k for k, v in self.seen.items() if now - float(v.get("last_seen", 0)) > 7 * 86400]:
            del self.seen[k]
        if len(self.seen) > self.SEEN_MAX:
            for k in sorted(self.seen, key=lambda k: float(self.seen[k].get("last_seen", 0)))[: len(self.seen) - self.SEEN_MAX]:
                del self.seen[k]
        if len(self.suppress) > self.LEDGER_MAX:
            self.suppress.clear()

    def flush(self) -> None:
        self.prune(time.time())
        atomic_write_json(
            self.state_path,
            {"version": OLA_VERSION, "ledger": self.ledger, "seen": self.seen,
             "backoff": self.backoff, "suppress": self.suppress},
        )


@dataclass
class FetchResult:
    """Data plus provenance, so stale data is never shown as fresh."""

    data: Any
    fetched: datetime
    from_cache: bool
    stale: bool
    error: str = ""

    def age_label(self, now: datetime) -> str:
        mins = max(0, int((now - self.fetched).total_seconds() // 60))
        if mins < 60:
            return f"{mins}M"
        if mins < 2880:
            return f"{mins // 60}H"
        return f"{mins // 1440}D"


def cached_fetch(
    store: Store,
    name: str,
    provider: str,
    refresh_s: float,
    max_age_s: float,
    fetch: Callable[[], Any],
    now: Optional[datetime] = None,
    allow_network: bool = True,
) -> FetchResult:
    """Return fresh cache, else fetch, else fall back to cache within max_age.

    `stale` is True whenever the returned data is older than refresh_s.
    Raises ProviderError if nothing usable is available.
    """
    now = now or _utcnow()
    cached = store.load(name)
    cached_at = _parse_iso(cached["fetched"]) if cached else None
    age = (now - cached_at).total_seconds() if cached_at else None
    if cached is not None and age is not None and 0 <= age < refresh_s:
        return FetchResult(cached["data"], cached_at, True, False)

    error = ""
    if allow_network and not store.in_backoff(provider, now.timestamp()):
        try:
            data = fetch()
            store.record_success(provider)
            rec = store.save(name, data, provider, now)
            return FetchResult(data, _parse_iso(rec["fetched"]) or now, False, False)
        except RateLimited as e:
            store.record_failure(provider, now.timestamp(), e.retry_after_s or 3600)
            error = "rate limited"
        except ProviderError as e:
            store.record_failure(provider, now.timestamp())
            error = str(e)
    elif allow_network:
        error = "backing off"
    else:
        error = "offline"
    _log(f"{provider} {name}: {error}")

    if cached is not None and age is not None and age < max_age_s:
        return FetchResult(cached["data"], cached_at, True, True, error)
    raise ProviderError(error or "no data")


# ---------------------------------------------------------------------------
# Launch Library 2
# ---------------------------------------------------------------------------

@dataclass
class Launch:
    """Normalized launch record (only the fields this script uses)."""

    id: str
    name: str
    mission: str
    rocket: str
    provider: str
    provider_abbrev: str
    pad: str
    location: str
    country: str
    pad_lat: Optional[float]
    pad_lon: Optional[float]
    net: datetime
    net_precision: str
    status_id: int
    status_name: str
    status_abbrev: str
    description: str
    url: str

    @property
    def outcome(self) -> bool:
        return self.status_id in LL2_OUTCOME_IDS


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _country_code(*candidates: Any) -> str:
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip().upper()
        if isinstance(c, dict):
            for k in ("alpha_3_code", "alpha_2_code"):
                v = c.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip().upper()
        if isinstance(c, list) and c:
            v = _country_code(c[0])
            if v:
                return v
    return ""


def _country_codes(raw: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for c in (_get(raw, "pad", "country"), _get(raw, "pad", "location", "country"),
              _get(raw, "pad", "country_code"), _get(raw, "pad", "location", "country_code")):
        if isinstance(c, dict):
            for k in ("alpha_3_code", "alpha_2_code"):
                v = c.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip().upper())
        elif isinstance(c, str) and c.strip():
            out.append(c.strip().upper())
    return out


def _float_or_none(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def normalize_launch(raw: Any) -> Optional[Dict[str, Any]]:
    """Validate one LL2 launch object and reduce it to a small JSON dict.

    Returns None for objects missing the required fields (id, name, net, status).
    Handles both the 2.3.0 and 2.2.0 response shapes.
    """
    if not isinstance(raw, dict):
        return None
    lid, name, net = raw.get("id"), raw.get("name"), _parse_iso(raw.get("net"))
    status = raw.get("status")
    if not isinstance(lid, str) or not lid or not isinstance(name, str) or net is None or not isinstance(status, dict):
        return None
    try:
        status_id = int(status.get("id"))
    except (TypeError, ValueError):
        return None
    rocket = (_get(raw, "rocket", "configuration", "name")
              or _get(raw, "rocket", "configuration", "full_name") or "")
    if not rocket and "|" in name:
        rocket = name.split("|", 1)[0].strip()
    mission = _get(raw, "mission", "name") or (name.split("|", 1)[1].strip() if "|" in name else name)
    lsp = raw.get("launch_service_provider") or {}
    prec = raw.get("net_precision")
    prec_name = (prec.get("abbrev") or prec.get("name") or "") if isinstance(prec, dict) else ""
    url = ""
    for u in (_get(raw, "mission", "info_urls"), raw.get("infoURLs"), raw.get("info_urls")):
        if isinstance(u, list) and u:
            first = u[0]
            url = first.get("url", "") if isinstance(first, dict) else (first if isinstance(first, str) else "")
            if url:
                break
    codes = _country_codes(raw)
    return {
        "id": lid,
        "name": name.strip(),
        "mission": str(mission or "").strip(),
        "rocket": str(rocket or "").strip(),
        "provider": str(lsp.get("name") or "").strip() if isinstance(lsp, dict) else "",
        "provider_abbrev": str(lsp.get("abbrev") or "").strip() if isinstance(lsp, dict) else "",
        "pad": str(_get(raw, "pad", "name") or "").strip(),
        "location": str(_get(raw, "pad", "location", "name") or "").strip(),
        "country": ",".join(dict.fromkeys(codes)),
        "pad_lat": _float_or_none(_get(raw, "pad", "latitude")),
        "pad_lon": _float_or_none(_get(raw, "pad", "longitude")),
        "net": _iso(net),
        "net_precision": str(prec_name),
        "status_id": status_id,
        "status_name": str(status.get("name") or "").strip(),
        "status_abbrev": str(status.get("abbrev") or "").strip(),
        "description": str(_get(raw, "mission", "description") or "").strip()[:500],
        "url": str(url or raw.get("url") or "").strip(),
    }


def launch_from_dict(d: Dict[str, Any]) -> Optional[Launch]:
    net = _parse_iso(d.get("net"))
    if net is None or not d.get("id"):
        return None
    try:
        return Launch(
            id=str(d["id"]), name=str(d.get("name", "")), mission=str(d.get("mission", "")),
            rocket=str(d.get("rocket", "")), provider=str(d.get("provider", "")),
            provider_abbrev=str(d.get("provider_abbrev", "")), pad=str(d.get("pad", "")),
            location=str(d.get("location", "")), country=str(d.get("country", "")),
            pad_lat=_float_or_none(d.get("pad_lat")), pad_lon=_float_or_none(d.get("pad_lon")),
            net=net, net_precision=str(d.get("net_precision", "")),
            status_id=int(d.get("status_id", 0)), status_name=str(d.get("status_name", "")),
            status_abbrev=str(d.get("status_abbrev", "")), description=str(d.get("description", "")),
            url=str(d.get("url", "")),
        )
    except (TypeError, ValueError):
        return None


def parse_ll2_page(payload: Any) -> List[Dict[str, Any]]:
    """Validate an LL2 list response and normalize its launches."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ProviderError("LL2 schema: missing results list")
    out = [n for n in (normalize_launch(r) for r in payload["results"]) if n]
    if payload["results"] and not out:
        raise ProviderError("LL2 schema: no valid launch objects")
    return out


def fetch_ll2(cfg: Config, which: str) -> List[Dict[str, Any]]:
    """Fetch /launches/upcoming/ or /launches/previous/ from LL2."""
    limit = cfg.launch_limit if which == "upcoming" else 10
    url = f"{cfg.ll2_base}/launches/{which}/?" + urllib.parse.urlencode({"limit": limit, "mode": "normal"})
    headers = {"Authorization": f"Token {cfg.ll2_api_key}"} if cfg.ll2_api_key else None
    _status_code, body = http_get(url, cfg.http_timeout, headers)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        raise ProviderError("LL2 returned non-JSON")
    if isinstance(payload, dict) and "detail" in payload and "results" not in payload:
        detail = str(payload.get("detail"))
        if "throttl" in detail.lower():
            raise RateLimited(f"LL2 throttled: {detail[:80]}")
        raise ProviderError(f"LL2 error: {detail[:80]}")
    return parse_ll2_page(payload)


def get_launches(cfg: Config, store: Store, which: str = "upcoming",
                 now: Optional[datetime] = None, allow_network: bool = True) -> Tuple[List[Launch], FetchResult]:
    """Return launches (cached per OLA_LAUNCH_REFRESH_MINUTES) and provenance."""
    refresh = cfg.launch_refresh_min * 60 * (1 if which == "upcoming" else 2)
    res = cached_fetch(store, f"ll2_{which}.json", "launchlibrary2", refresh,
                       cfg.launch_max_age_h * 3600, lambda: fetch_ll2(cfg, which), now, allow_network)
    launches = [l for l in (launch_from_dict(d) for d in (res.data or []) if isinstance(d, dict)) if l]
    launches.sort(key=lambda l: l.net)
    return launches, res


def _site_preset_terms(term: str) -> Tuple[str, ...]:
    return SITE_PRESETS.get(term, (term,))


def launch_matches(cfg: Config, l: Launch) -> bool:
    """Apply OLA_LAUNCH_* filters. AND across categories, OR within one."""
    if cfg.launch_countries:
        codes = set(l.country.split(",")) if l.country else set()
        if not codes & set(cfg.launch_countries):
            return False
    if cfg.launch_agencies:
        hay = f"{l.provider} {l.provider_abbrev}".lower()
        if not any(a in hay for a in cfg.launch_agencies):
            return False
    if cfg.launch_sites:
        hay = f"{l.pad} {l.location}".lower()
        if not any(t in hay for s in cfg.launch_sites for t in _site_preset_terms(s)):
            return False
    if cfg.launch_rockets:
        if not any(r in l.rocket.lower() for r in cfg.launch_rockets):
            return False
    if cfg.launch_missions:
        hay = f"{l.mission} {l.name}".lower()
        if not any(m in hay for m in cfg.launch_missions):
            return False
    return True


def short_site(l: Launch) -> str:
    hay = f"{l.location} {l.pad}".lower()
    for needle, abbrev in SITE_ABBREVIATIONS:
        if needle in hay:
            return abbrev
    base = (l.location or l.pad or "?").split(",")[0].strip()
    return base.upper()[:16]


def short_rocket(l: Launch, max_len: int = 18) -> str:
    r = (l.rocket or l.name.split("|")[0]).strip().upper()
    r = re.sub(r"\s+BLOCK\s+\d+$", "", r)
    return r[:max_len].strip()


def short_mission(l: Launch, max_len: int = 24) -> str:
    m = (l.mission or "").strip()
    m = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", m)  # drop trailing parenthetical
    if len(m) > max_len:
        m = m[: max_len - 1].rstrip() + "…"
    return m


def fmt_distance(cfg: Config, l: Launch) -> str:
    if not (cfg.show_distance and cfg.has_location and l.pad_lat is not None and l.pad_lon is not None):
        return ""
    km = haversine_km(cfg.lat, cfg.lon, l.pad_lat, l.pad_lon)  # type: ignore[arg-type]
    if cfg.units == "km":
        return f"{km:.0f}KM AWAY"
    return f"{km / 1.609344:.0f}MI AWAY"


def fmt_local(cfg: Config, dt: datetime, now: datetime, with_day: Optional[bool] = None) -> str:
    """Local time label like '20:15 EST' or 'Thu 20:15 EST' (labelled zone)."""
    loc = dt.astimezone(cfg.tz)
    tzabbr = loc.strftime("%Z") or cfg.tz_name
    if with_day is None:
        with_day = loc.date() != now.astimezone(cfg.tz).date()
    s = loc.strftime("%a %H:%M" if with_day else "%H:%M")
    return f"{s} {tzabbr}"


def status_tag(l: Launch) -> str:
    return {LL2_GO: "GO", LL2_TBD: "TBD", LL2_HOLD: "HOLD", LL2_TBC: "TBC",
            LL2_IN_FLIGHT: "IN FLIGHT", LL2_SUCCESS: "SUCCESS", LL2_FAILURE: "FAILURE",
            LL2_PARTIAL: "PARTIAL FAILURE"}.get(l.status_id, (l.status_abbrev or l.status_name or "?").upper())


def fmt_launch_line(cfg: Config, l: Launch, now: datetime) -> str:
    """`🚀 FALCON 9 | Starlink 10-12 | CCSFS | Thu 18:30 EST | T-5H12M | GO`"""
    when = fmt_local(cfg, l.net, now)
    if l.status_id == LL2_TBD or l.net_precision.lower() not in ("", "sec", "second", "min", "minute"):
        when = f"NET {when}"
    parts = [f"🚀 {short_rocket(l)}", short_mission(l), short_site(l), when,
             fmt_countdown(l.net - now), status_tag(l), fmt_distance(cfg, l)]
    return fit_parts(parts, cfg.max_len)


# ---------------------------------------------------------------------------
# Launch notifications (thresholds, changes, outcomes)
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    type: str
    message: str
    key: str
    data: Dict[str, Any] = field(default_factory=dict)


def launch_alerts(cfg: Config, store: Store, upcoming: List[Launch], previous: List[Launch],
                  now: datetime) -> List[Alert]:
    """Compute due launch notifications and update the ledger.

    - Threshold alerts fire once per (launch id, threshold, NET).
    - When a NET changes, threshold alerts for that launch are re-armed and a
      single delay / reschedule alert is produced.
    - Outcomes are only reported when LL2 itself reports Success / Failure /
      Partial Failure (or In Flight). Nothing is inferred from the clock.
    """
    alerts: List[Alert] = []
    ts = now.timestamp()
    thresholds = cfg.alert_thresholds_min
    grace = timedelta(minutes=cfg.t0_grace_min)

    for l in upcoming:
        if not launch_matches(cfg, l):
            continue
        net_iso = _iso(l.net)
        prev = store.seen.get(l.id)
        rocket = short_rocket(l)
        site = short_site(l)

        # --- NET / status changes for launches we have seen before ----------
        if prev:
            old_net = _parse_iso(prev.get("net"))
            if old_net and old_net != l.net and abs((l.net - old_net).total_seconds()) >= 60:
                store.forget(f"launch:{l.id}:T", keep=lambda k, v: v.get("net") == net_iso)
                later = l.net > old_net
                key = f"launch:{l.id}:net:{net_iso}"
                if cfg.alert_delays and not store.was_sent(key) and l.net > now - grace:
                    head = "⚠️ LAUNCH DELAYED" if later else "🔁 LAUNCH RESCHEDULED"
                    msg = fit_parts([head, rocket, short_mission(l), f"NEW {fmt_local(cfg, l.net, now)}",
                                     fmt_countdown(l.net - now)], cfg.max_len)
                    alerts.append(Alert("launch_change", msg, key,
                                        {"id": l.id, "old_net": _iso(old_net), "net": net_iso,
                                         "change": "delayed" if later else "rescheduled"}))
                    store.mark_sent(key, ts)
            old_status = int(prev.get("status_id", 0) or 0)
            if old_status != l.status_id:
                key = f"launch:{l.id}:status:{l.status_id}"
                msg = ""
                if l.status_id == LL2_HOLD and cfg.alert_holds:
                    msg = fit_parts(["⏸️ LAUNCH HOLD", rocket, short_mission(l), site], cfg.max_len)
                elif "cancel" in l.status_name.lower() and cfg.alert_cancellations:
                    msg = fit_parts(["❌ LAUNCH CANCELED", rocket, short_mission(l)], cfg.max_len)
                elif l.status_id == LL2_IN_FLIGHT and cfg.alert_in_flight:
                    msg = fit_parts(["🚀 LIFTOFF CONFIRMED", rocket, short_mission(l), "IN FLIGHT (LL2)"], cfg.max_len)
                if msg and not store.was_sent(key):
                    alerts.append(Alert("launch_status", msg, key,
                                        {"id": l.id, "status": l.status_name, "old_status_id": old_status}))
                    store.mark_sent(key, ts)

        store.seen[l.id] = {"net": net_iso, "status_id": l.status_id, "name": l.name,
                            "rocket": rocket, "last_seen": ts}

        # --- countdown thresholds ---------------------------------------------
        if l.status_id in LL2_OUTCOME_IDS or l.status_id == LL2_IN_FLIGHT:
            continue
        remaining = l.net - now
        due = [t for t in thresholds if (t > 0 and timedelta(0) < remaining <= timedelta(minutes=t))
               or (t == 0 and -grace <= remaining <= timedelta(0))]
        if not due:
            continue
        tightest = min(due)
        # Mark larger thresholds as covered so a late start never bursts.
        for t in due:
            key = f"launch:{l.id}:T{t}"
            if t != tightest and not store.was_sent(key):
                store.mark_sent(key, ts, net=net_iso, skipped=True)
        key = f"launch:{l.id}:T{tightest}"
        if store.was_sent(key):
            continue
        if tightest == 0:
            msg = fit_parts([f"🕒 SCHEDULED T-0 | {rocket}", short_mission(l), site,
                             "LIFTOFF NOT YET CONFIRMED"], cfg.max_len)
        else:
            msg = fit_parts([f"🚀 {rocket}", site, fmt_countdown(remaining), short_mission(l),
                             fmt_local(cfg, l.net, now), status_tag(l), fmt_distance(cfg, l)], cfg.max_len)
        alerts.append(Alert("launch_alert", msg, key, {"id": l.id, "threshold_min": tightest, "net": net_iso,
                                                       "status": l.status_name, "url": l.url}))
        store.mark_sent(key, ts, net=net_iso)

    # Launches that vanished from the upcoming list before their NET: the
    # source no longer schedules them. Reported as removed, not "canceled".
    # Only launches inside the window the list covers count (a far-future
    # launch can fall off the end of a limited page without being removed),
    # and filtering never makes a launch look removed.
    listed_ids = {l.id for l in upcoming}
    window_end = max((l.net for l in upcoming), default=now)
    for lid, prev in list(store.seen.items()):
        if lid in listed_ids or not cfg.alert_cancellations or not upcoming:
            continue
        old_net = _parse_iso(prev.get("net"))
        if not old_net or old_net <= now + timedelta(hours=1) or old_net >= window_end:
            continue
        if any(p.id == lid for p in previous):
            continue
        key = f"launch:{lid}:removed"
        if store.was_sent(key) or prev.get("removed"):
            continue
        msg = fit_parts(["⚠️ LAUNCH REMOVED FROM SCHEDULE", str(prev.get("rocket") or prev.get("name") or "?")],
                        cfg.max_len)
        alerts.append(Alert("launch_status", msg, key, {"id": lid, "status": "removed"}))
        store.mark_sent(key, ts)
        prev["removed"] = True

    # Outcomes: only what LL2 reports, only for recent launches we care about.
    if cfg.alert_outcomes:
        for l in previous:
            if not l.outcome or not launch_matches(cfg, l):
                continue
            if now - l.net > timedelta(hours=OUTCOME_WINDOW_H) or l.net > now:
                continue
            key = f"launch:{l.id}:outcome:{l.status_id}"
            if store.was_sent(key):
                continue
            head = {LL2_SUCCESS: "✅ LAUNCH SUCCESS", LL2_FAILURE: "❌ LAUNCH FAILURE",
                    LL2_PARTIAL: "⚠️ PARTIAL FAILURE"}[l.status_id]
            msg = fit_parts([head, short_rocket(l), short_mission(l), site_label(l)], cfg.max_len)
            alerts.append(Alert("launch_outcome", msg, key, {"id": l.id, "status": l.status_name}))
            store.mark_sent(key, ts)
    return alerts


def site_label(l: Launch) -> str:
    return short_site(l)


# ---------------------------------------------------------------------------
# CelesTrak GP data + local SGP4 propagation
# ---------------------------------------------------------------------------

OMM_REQUIRED = ("OBJECT_NAME", "NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT",
                "MEAN_MOTION_DDOT")


def validate_omm(rec: Any) -> Optional[Dict[str, Any]]:
    """Keep only well-formed OMM records (CelesTrak FORMAT=json)."""
    if not isinstance(rec, dict) or any(k not in rec for k in OMM_REQUIRED):
        return None
    try:
        for k in OMM_REQUIRED[3:]:
            if not math.isfinite(float(rec[k])):
                return None
        norad = int(rec["NORAD_CAT_ID"])
    except (TypeError, ValueError):
        return None
    if norad <= 0 or _parse_iso(str(rec["EPOCH"])) is None:
        return None
    keep = {k: rec[k] for k in OMM_REQUIRED}
    for k, default in (("OBJECT_ID", ""), ("CLASSIFICATION_TYPE", "U"), ("EPHEMERIS_TYPE", 0),
                       ("ELEMENT_SET_NO", 999), ("REV_AT_EPOCH", 0)):
        keep[k] = rec.get(k, default)
    return keep


def parse_gp_payload(body: bytes) -> List[Dict[str, Any]]:
    """Parse a CelesTrak GP JSON response. An unknown CATNR yields []."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text or text.lower().startswith("no gp data"):
        return []
    try:
        payload = json.loads(text)
    except ValueError:
        raise ProviderError("CelesTrak returned non-JSON")
    if not isinstance(payload, list):
        raise ProviderError("CelesTrak schema: expected a list")
    out = [r for r in (validate_omm(x) for x in payload) if r]
    if payload and not out:
        raise ProviderError("CelesTrak schema: no valid OMM records")
    return out


def fetch_gp(cfg: Config, query: Dict[str, str]) -> List[Dict[str, Any]]:
    q = dict(query)
    q["FORMAT"] = "json"
    url = f"{cfg.celestrak_gp_url}?{urllib.parse.urlencode(q)}"
    try:
        _status_code, body = http_get(url, cfg.http_timeout)
    except ProviderError as e:
        if e.code == 403:  # CelesTrak blocks clients that re-download too often
            raise RateLimited("CelesTrak 403 (download limit)", 7200)
        raise
    return parse_gp_payload(body)


def get_gp(cfg: Config, store: Store, group: Optional[str] = None, catnr: Optional[int] = None,
           now: Optional[datetime] = None, allow_network: bool = True) -> Tuple[List[Dict[str, Any]], FetchResult]:
    """Return OMM records for a CelesTrak group or one catalog number (cached)."""
    if catnr is not None:
        name, query = f"gp_catnr_{int(catnr)}.json", {"CATNR": str(int(catnr))}
    else:
        name, query = f"gp_group_{group}.json", {"GROUP": str(group)}
    res = cached_fetch(store, name, "celestrak", cfg.gp_refresh_hours * 3600,
                       30 * 86400, lambda: fetch_gp(cfg, query)[:GP_GROUP_CACHE_MAX], now, allow_network)
    return [r for r in (res.data or []) if isinstance(r, dict)], res


def element_epoch(rec: Dict[str, Any]) -> Optional[datetime]:
    return _parse_iso(str(rec.get("EPOCH", "")))


def element_age_days(rec: Dict[str, Any], at: datetime) -> float:
    ep = element_epoch(rec)
    return float("inf") if ep is None else abs((at - ep).total_seconds()) / 86400.0


def make_satrec(rec: Dict[str, Any]) -> Any:
    """Initialise an sgp4 Satrec from an OMM record (requires `sgp4`)."""
    if not HAVE_SGP4:
        raise ProviderError("sgp4 not installed")
    fields = {k: (str(v) if k in ("EPOCH", "OBJECT_ID", "CLASSIFICATION_TYPE") else v) for k, v in rec.items()}
    ep = fields["EPOCH"]
    if "." not in ep:
        fields["EPOCH"] = ep.rstrip("Z") + ".000000"
    else:
        fields["EPOCH"] = ep.rstrip("Z")
    sat = Satrec()
    _sgp4_omm.initialize(sat, fields)
    return sat


def _jd(dt: datetime) -> Tuple[float, float]:
    """Julian date split into (whole, fraction) for sgp4."""
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = dt.day
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd0 = math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5
    fr = (dt.hour + dt.minute / 60.0 + (dt.second + dt.microsecond / 1e6) / 3600.0) / 24.0
    return jd0, fr


def gmst_rad(jd: float, fr: float) -> float:
    """Greenwich mean sidereal time (IAU-82), UT1 approximated by UTC."""
    t = ((jd - 2451545.0) + fr) / 36525.0
    g = 67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3
    return math.radians((g % 86400.0) / 240.0)


def observer_ecef(lat: float, lon: float, alt_m: float) -> Tuple[float, float, float]:
    """WGS84 geodetic -> ECEF (km)."""
    la, lo = math.radians(lat), math.radians(lon)
    e2 = WGS84_F * (2 - WGS84_F)
    n = EARTH_RADIUS_KM / math.sqrt(1 - e2 * math.sin(la) ** 2)
    h = alt_m / 1000.0
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - e2) + h) * math.sin(la))


def teme_to_ecef(r: Tuple[float, float, float], gmst: float) -> Tuple[float, float, float]:
    c, s = math.cos(gmst), math.sin(gmst)
    return (c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2])


def look_angles(lat: float, lon: float, obs: Tuple[float, float, float],
                tgt: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Azimuth, elevation (deg) and range (km) from observer to an ECEF target."""
    la, lo = math.radians(lat), math.radians(lon)
    dx, dy, dz = tgt[0] - obs[0], tgt[1] - obs[1], tgt[2] - obs[2]
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    e = -so * dx + co * dy
    n = -sl * co * dx - sl * so * dy + cl * dz
    u = cl * co * dx + cl * so * dy + sl * dz
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    el = math.degrees(math.asin(max(-1.0, min(1.0, u / rng)))) if rng else 90.0
    az = math.degrees(math.atan2(e, n)) % 360.0
    return az, el, rng


def sun_eci(jd: float, fr: float) -> Tuple[float, float, float]:
    """Low-precision solar position (Astronomical Almanac), km, true-of-date."""
    n = (jd - 2451545.0) + fr
    L = math.radians((280.460 + 0.9856474 * n) % 360.0)
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    r = (1.00014 - 0.01671 * math.cos(g) - 0.00014 * math.cos(2 * g)) * AU_KM
    return (r * math.cos(lam), r * math.cos(eps) * math.sin(lam), r * math.sin(eps) * math.sin(lam))


def sun_altitude(lat: float, lon: float, dt: datetime) -> float:
    """Apparent-geometry solar altitude in degrees at the observer."""
    jd, fr = _jd(dt)
    s = teme_to_ecef(sun_eci(jd, fr), gmst_rad(jd, fr))
    obs = observer_ecef(lat, lon, 0.0)
    return look_angles(lat, lon, obs, s)[1]


def is_sunlit(r_sat: Tuple[float, float, float], r_sun: Tuple[float, float, float]) -> bool:
    """Cylindrical Earth-shadow test (both vectors in the same inertial frame)."""
    sm = math.sqrt(sum(c * c for c in r_sun))
    sh = tuple(c / sm for c in r_sun)
    proj = sum(a * b for a, b in zip(r_sat, sh))
    if proj >= 0:
        return True
    perp = math.sqrt(max(0.0, sum(c * c for c in r_sat) - proj * proj))
    return perp > EARTH_RADIUS_KM


@dataclass
class SatPass:
    norad: int
    name: str
    aos: datetime
    tca: datetime
    los: datetime
    max_el: float
    aos_az: float
    max_az: float
    los_az: float
    visible: Optional[bool]  # None = illumination not evaluated
    visible_start: Optional[datetime] = None
    element_age_days: float = 0.0

    @property
    def duration_s(self) -> int:
        return int((self.los - self.aos).total_seconds())

    @property
    def duration_min(self) -> int:
        return max(1, int(round(self.duration_s / 60.0)))

    def to_dict(self) -> Dict[str, Any]:
        return {"norad": self.norad, "name": self.name, "aos": _iso(self.aos), "tca": _iso(self.tca),
                "los": _iso(self.los), "max_el": round(self.max_el, 1), "aos_az": round(self.aos_az),
                "max_az": round(self.max_az), "los_az": round(self.los_az), "duration_s": self.duration_s,
                "visible": self.visible, "element_age_days": round(self.element_age_days, 2)}


class Propagator:
    """Wraps one Satrec + observer for az/el/illumination at a time."""

    def __init__(self, rec: Dict[str, Any], lat: float, lon: float, alt_m: float):
        self.sat = make_satrec(rec)
        self.lat, self.lon = lat, lon
        self.obs = observer_ecef(lat, lon, alt_m)

    def state(self, dt: datetime) -> Optional[Tuple[float, float, Tuple[float, float, float], float, float]]:
        jd, fr = _jd(dt)
        err, r, _v = self.sat.sgp4(jd, fr)
        if err != 0:
            return None
        g = gmst_rad(jd, fr)
        az, el, _ = look_angles(self.lat, self.lon, self.obs, teme_to_ecef(r, g))
        return az, el, r, jd, fr

    def azel(self, dt: datetime) -> Optional[Tuple[float, float]]:
        st = self.state(dt)
        return None if st is None else (st[0], st[1])


def _sat_display_name(rec: Dict[str, Any]) -> str:
    norad = int(rec["NORAD_CAT_ID"])
    if norad == ISS_NORAD:
        return "ISS"
    if norad == TIANGONG_NORAD:
        return "TIANGONG"
    name = str(rec.get("OBJECT_NAME") or norad).strip().upper()
    name = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    return name[:20] or str(norad)


def find_passes(rec: Dict[str, Any], lat: float, lon: float, alt_m: float, start: datetime,
                hours: float, min_el: float, step_s: int = 30, evaluate_visibility: bool = True,
                deadline: Optional[float] = None) -> List[SatPass]:
    """Predict passes above the horizon that peak at >= min_el.

    Coarse scan at `step_s`, then bisection to ~1 s for AOS/LOS and a ternary
    search for the culmination. Visibility uses solar altitude (<= -6 deg) and
    a cylindrical shadow model; it is an estimate, never a guarantee.
    """
    prop = Propagator(rec, lat, lon, alt_m)
    name = _sat_display_name(rec)
    norad = int(rec["NORAD_CAT_ID"])
    age = element_age_days(rec, start)
    end = start + timedelta(hours=hours)
    passes: List[SatPass] = []

    def el_at(t: datetime) -> float:
        a = prop.azel(t)
        return -90.0 if a is None else a[1]

    def bisect(t0: datetime, t1: datetime, rising: bool) -> datetime:
        for _ in range(20):
            if (t1 - t0).total_seconds() <= 1:
                break
            mid = t0 + (t1 - t0) / 2
            above = el_at(mid) > 0
            if above == rising:
                t1 = mid
            else:
                t0 = mid
        return t1 if rising else t0

    t = start
    prev_el = el_at(t)
    in_pass = prev_el > 0
    aos = start if in_pass else None
    step = timedelta(seconds=step_s)
    while t < end:
        if deadline is not None and time.monotonic() > deadline:
            break
        t2 = min(t + step, end)
        el2 = el_at(t2)
        if not in_pass and el2 > 0 and prev_el <= 0:
            aos = bisect(t, t2, rising=True)
            in_pass = True
        elif in_pass and el2 <= 0:
            los = bisect(t, t2, rising=False)
            p = _build_pass(prop, name, norad, aos or start, los, min_el, evaluate_visibility, age)
            if p:
                passes.append(p)
            in_pass, aos = False, None
        prev_el = el2
        t = t2
    return passes


def _build_pass(prop: Propagator, name: str, norad: int, aos: datetime, los: datetime, min_el: float,
                evaluate_visibility: bool, age: float) -> Optional[SatPass]:
    if los <= aos:
        return None
    lo, hi = aos, los
    for _ in range(40):
        if (hi - lo).total_seconds() <= 1:
            break
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        if (prop.azel(m1) or (0, -90))[1] < (prop.azel(m2) or (0, -90))[1]:
            lo = m1
        else:
            hi = m2
    tca = lo + (hi - lo) / 2
    az_max, el_max = prop.azel(tca) or (0.0, -90.0)
    if el_max < min_el:
        return None
    az_aos = (prop.azel(aos) or (0.0, 0.0))[0]
    az_los = (prop.azel(los) or (0.0, 0.0))[0]
    visible: Optional[bool] = None
    vis_start: Optional[datetime] = None
    if evaluate_visibility:
        visible = False
        t = aos
        while t <= los:
            st = prop.state(t)
            if st is not None:
                _az, el, r, jd, fr = st
                if el >= VISIBLE_MIN_EL_DEG and sun_altitude(prop.lat, prop.lon, t) <= DARK_SUN_ALT_DEG \
                        and is_sunlit(r, sun_eci(jd, fr)):
                    visible = True
                    vis_start = t
                    break
            t += timedelta(seconds=20)
    return SatPass(norad, name, aos, tca, los, el_max, az_aos, az_max, az_los, visible, vis_start, age)


# ---------------------------------------------------------------------------
# Pass formatting
# ---------------------------------------------------------------------------

def fmt_pass(cfg: Config, p: SatPass, now: datetime, visible_style: bool = False) -> str:
    """`🛰️ ISS | 20:15 EST | MAX 65° | 6MIN | NW→SE`"""
    t = fmt_local(cfg, p.aos, now)
    path = f"{compass(p.aos_az)}→{compass(p.los_az)}"
    if visible_style and p.visible:
        start = p.visible_start or p.aos
        return fit_parts([f"🌌 {p.name} VISIBLE", fmt_local(cfg, start, now), f"MAX {p.max_el:.0f}°",
                          f"{p.duration_min}MIN", path, "EST. NOT GUARANTEED"], cfg.max_len)
    tag = ""
    if p.visible is True:
        tag = "LIKELY VISIBLE"
    if p.norad == ISS_NORAD or p.name in ("ISS", "TIANGONG"):
        parts = [f"🛰️ {p.name}", t, f"MAX {p.max_el:.0f}°", f"{p.duration_min}MIN", path, tag]
    else:
        parts = [f"🛰️ {p.name}", f"AOS {t}", f"MAX {p.max_el:.0f}°",
                 f"LOS {fmt_local(cfg, p.los, now, with_day=False)}", f"{p.duration_min}MIN", path, f"#{p.norad}", tag]
    return fit_parts(parts, cfg.max_len)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Engine:
    """Ties configuration, cache/state, providers and formatting together."""

    def __init__(self, cfg: Config, store: Optional[Store] = None, now_fn: Callable[[], datetime] = _utcnow,
                 allow_network: bool = True, in_meshmonitor: bool = False):
        self.cfg = cfg
        self.store = store or Store(cfg.cache_dir)
        self.now_fn = now_fn
        self.allow_network = allow_network
        self.in_meshmonitor = in_meshmonitor
        self.notes: List[str] = []

    # -- data accessors --------------------------------------------------

    def launches(self, which: str = "upcoming") -> Tuple[List[Launch], FetchResult]:
        return get_launches(self.cfg, self.store, which, self.now_fn(), self.allow_network)

    def upcoming_filtered(self) -> Tuple[List[Launch], FetchResult]:
        now = self.now_fn()
        launches, res = self.launches("upcoming")
        horizon = now - timedelta(minutes=self.cfg.t0_grace_min)
        out = [l for l in launches if launch_matches(self.cfg, l) and l.net >= horizon
               and l.status_id not in LL2_OUTCOME_IDS]
        return out, res

    def _deadline(self) -> float:
        return time.monotonic() + self.cfg.compute_budget_s

    def gp_records(self, ids: Optional[List[int]] = None, groups: Optional[List[str]] = None
                   ) -> Tuple[List[Dict[str, Any]], List[FetchResult], List[str]]:
        """Collect OMM records for catalog ids and/or groups. Returns (records, provenance, errors)."""
        now = self.now_fn()
        recs: Dict[int, Dict[str, Any]] = {}
        provs: List[FetchResult] = []
        errs: List[str] = []
        for cid in ids or []:
            try:
                rs, res = get_gp(self.cfg, self.store, catnr=cid, now=now, allow_network=self.allow_network)
                provs.append(res)
                if not rs:
                    errs.append(f"#{cid} not found")
                for r in rs:
                    recs[int(r["NORAD_CAT_ID"])] = r
            except ProviderError as e:
                errs.append(f"#{cid}: {e}")
        for g in groups or []:
            try:
                rs, res = get_gp(self.cfg, self.store, group=g, now=now, allow_network=self.allow_network)
                provs.append(res)
                for r in rs[: self.cfg.max_satellites]:
                    recs.setdefault(int(r["NORAD_CAT_ID"]), r)
            except ProviderError as e:
                errs.append(f"{g}: {e}")
        return list(recs.values()), provs, errs

    def passes(self, recs: List[Dict[str, Any]], min_el: Optional[float] = None,
               visibility: bool = True, hours: Optional[float] = None) -> Tuple[List[SatPass], List[str]]:
        """Predict passes for records, skipping expired elements."""
        if not HAVE_SGP4:
            raise ProviderError("sgp4 not installed")
        cfg = self.cfg
        if not cfg.has_location:
            raise ProviderError("location not set")
        now = self.now_fn()
        out: List[SatPass] = []
        notes: List[str] = []
        deadline = self._deadline()
        for rec in recs[: cfg.max_satellites]:
            age = element_age_days(rec, now)
            if age > cfg.max_element_age_days:
                notes.append(f"{_sat_display_name(rec)} elements expired ({age:.0f}D)")
                continue
            try:
                out.extend(find_passes(rec, cfg.lat, cfg.lon, cfg.alt_m, now,  # type: ignore[arg-type]
                                       hours or cfg.lookahead_hours,
                                       cfg.min_elevation_deg if min_el is None else min_el,
                                       evaluate_visibility=visibility, deadline=deadline))
            except ProviderError:
                raise
            except Exception as e:  # bad element set -> skip that object only
                notes.append(f"{_sat_display_name(rec)} propagation error")
                _log(f"propagation error {rec.get('NORAD_CAT_ID')}: {e}")
            if time.monotonic() > deadline:
                notes.append("compute budget reached")
                break
        out.sort(key=lambda p: p.aos)
        return out, notes

    @staticmethod
    def no_pass_problem(notes: List[str]) -> Optional[str]:
        """Explain an empty result caused by data problems (never hide them)."""
        expired = [n for n in notes if "expired" in n]
        if expired:
            return f"⚠️ {expired[0].upper()} | refresh needed"
        errors = [n for n in notes if "error" in n]
        if errors:
            return f"⚠️ {errors[0].upper()}"
        return None

    @staticmethod
    def stale_suffix(provs: List[FetchResult], now: datetime) -> str:
        stale = [p for p in provs if p.stale]
        if not stale:
            return ""
        oldest = min(stale, key=lambda p: p.fetched)
        return f"CACHED {oldest.age_label(now)}"

    # -- satellite commands ---------------------------------------------

    def _need_sat_prereqs(self) -> Optional[str]:
        if not HAVE_SGP4:
            return "⚠️ Satellite tracking needs sgp4 (pip install sgp4)"
        if not self.cfg.has_location:
            return "⚠️ Set OLA_LAT/OLA_LON for pass predictions"
        return None

    def cmd_iss(self) -> str:
        return self.cmd_sat(ISS_NORAD)

    def cmd_sat(self, norad: int) -> str:
        pre = self._need_sat_prereqs()
        if pre:
            return pre
        now = self.now_fn()
        recs, provs, errs = self.gp_records(ids=[norad])
        if not recs:
            if errs and "not found" in errs[0]:
                return f"⚠️ NORAD {norad} not found in CelesTrak data"
            return f"⚠️ No orbital data for #{norad} (offline, no cache)"
        try:
            passes, notes = self.passes(recs)
        except ProviderError as e:
            return f"⚠️ {e}"
        if not passes and self.no_pass_problem(notes):
            return self.no_pass_problem(notes) or ""
        name = _sat_display_name(recs[0])
        stale = self.stale_suffix(provs, now)
        if not passes:
            return fit_parts([f"🛰️ {name}", f"No passes ≥{self.cfg.min_elevation_deg:.0f}° in {self.cfg.lookahead_hours}h",
                              stale], self.cfg.max_len)
        line = fmt_pass(self.cfg, passes[0], now)
        return fit_parts([line, stale], self.cfg.max_len) if stale else line

    def _satellite_targets(self) -> Tuple[List[int], List[str]]:
        cfg = self.cfg
        if cfg.mode == "iss":
            return [ISS_NORAD], []
        return list(cfg.satellite_ids), list(cfg.satellite_groups)

    def cmd_satellites(self) -> str:
        pre = self._need_sat_prereqs()
        if pre:
            return pre
        now = self.now_fn()
        ids, groups = self._satellite_targets()
        recs, provs, errs = self.gp_records(ids=ids, groups=groups)
        if not recs:
            return "⚠️ No orbital data (offline, no cache)" if errs else "🛰️ No satellites configured"
        try:
            passes, notes = self.passes(recs, visibility=False)
        except ProviderError as e:
            return f"⚠️ {e}"
        stale = self.stale_suffix(provs, now)
        if not passes and self.no_pass_problem(notes):
            return self.no_pass_problem(notes) or ""
        if not passes:
            return fit_parts([f"🛰️ No passes ≥{self.cfg.min_elevation_deg:.0f}° in next {self.cfg.lookahead_hours}h",
                              stale], self.cfg.max_len)
        items = [f"{p.name} {fmt_local(self.cfg, p.aos, now)} {p.max_el:.0f}°" for p in passes]
        return self._summary("🛰️", items, stale)

    def cmd_visible(self) -> str:
        pre = self._need_sat_prereqs()
        if pre:
            return pre
        now = self.now_fn()
        ids, groups = self._satellite_targets()
        if self.cfg.mode not in ("iss",) and not ids and not groups:
            ids = [ISS_NORAD]
        if ISS_NORAD not in ids and "stations" not in groups:
            ids = [ISS_NORAD] + ids
        recs, provs, errs = self.gp_records(ids=ids, groups=groups)
        if not recs:
            return "⚠️ No orbital data (offline, no cache)"
        try:
            passes, notes = self.passes(recs, min_el=VISIBLE_MIN_EL_DEG, visibility=True)
        except ProviderError as e:
            return f"⚠️ {e}"
        vis = [p for p in passes if p.visible]
        stale = self.stale_suffix(provs, now)
        if not passes and self.no_pass_problem(notes):
            return self.no_pass_problem(notes) or ""
        if not vis:
            return fit_parts([f"🌌 No predicted visible passes in {self.cfg.lookahead_hours}h", stale], self.cfg.max_len)
        if len(vis) == 1:
            return fit_parts([fmt_pass(self.cfg, vis[0], now, visible_style=True), stale], self.cfg.max_len)
        items = [f"{p.name} {fmt_local(self.cfg, p.visible_start or p.aos, now)} {p.max_el:.0f}°" for p in vis]
        return self._summary("🌌 VISIBLE (est.)", items, stale)

    def _summary(self, head: str, items: List[str], tail: str = "") -> str:
        out = head
        used = 0
        for it in items:
            cand = f"{out} | {it}" if used else f"{out} {it}"
            reserve = len(f" | {tail}") if tail else 0
            if len(cand) + reserve > self.cfg.max_len:
                break
            out, used = cand, used + 1
        if used < len(items):
            more = f" +{len(items) - used}"
            if len(out) + len(more) + (len(f" | {tail}") if tail else 0) <= self.cfg.max_len:
                out += more
        if tail:
            out = f"{out} | {tail}"
        return clamp_text(out, self.cfg.max_len)

    # -- launch commands ---------------------------------------------------

    def cmd_launch(self) -> str:
        now = self.now_fn()
        try:
            launches, res = self.upcoming_filtered()
        except ProviderError as e:
            return f"⚠️ Launch data unavailable ({e})"
        stale = self.stale_suffix([res], now)
        if not launches:
            return fit_parts(["🚀 No upcoming launches match filters", stale], self.cfg.max_len)
        line = fmt_launch_line(self.cfg, launches[0], now)
        return fit_parts([line, stale], self.cfg.max_len) if stale else line

    def cmd_launches(self) -> str:
        now = self.now_fn()
        try:
            launches, res = self.upcoming_filtered()
        except ProviderError as e:
            return f"⚠️ Launch data unavailable ({e})"
        stale = self.stale_suffix([res], now)
        soon = [l for l in launches if l.net <= now + timedelta(hours=24)]
        if not soon:
            nxt = f"next {fmt_countdown(launches[0].net - now)} {short_rocket(launches[0])}" if launches else ""
            return fit_parts(["🚀 No launches in next 24h", nxt, stale], self.cfg.max_len)
        items = [f"{short_rocket(l)} {fmt_local(self.cfg, l.net, now)} {short_site(l)}" for l in soon]
        return self._summary(f"🚀 {len(soon)} in 24h:", items, stale)

    # -- combined ----------------------------------------------------------

    def cmd_next(self) -> str:
        """Soonest relevant event for the configured mode."""
        mode = self.cfg.mode
        now = self.now_fn()
        candidates: List[Tuple[datetime, str]] = []
        problems: List[str] = []
        if mode in ("launches", "orbit_and_launch"):
            try:
                launches, res = self.upcoming_filtered()
                if launches:
                    line = fmt_launch_line(self.cfg, launches[0], now)
                    st = self.stale_suffix([res], now)
                    candidates.append((launches[0].net, fit_parts([line, st], self.cfg.max_len) if st else line))
            except ProviderError as e:
                problems.append(f"launch data {e}")
        if mode in ("satellites", "iss", "orbit_and_launch") and self._need_sat_prereqs() is None:
            ids, groups = self._satellite_targets()
            if mode == "orbit_and_launch" and not ids and not groups:
                ids = [ISS_NORAD]
            recs, provs, errs = self.gp_records(ids=ids, groups=groups)
            if recs:
                try:
                    passes, _ = self.passes(recs)
                    if passes:
                        st = self.stale_suffix(provs, now)
                        line = fmt_pass(self.cfg, passes[0], now)
                        candidates.append((passes[0].aos, fit_parts([line, st], self.cfg.max_len) if st else line))
                except ProviderError as e:
                    problems.append(str(e))
            elif errs:
                problems.append("orbital data unavailable")
        elif mode in ("satellites", "iss"):
            return self._need_sat_prereqs() or ""
        if candidates:
            candidates.sort(key=lambda c: c[0])
            return candidates[0][1]
        if problems:
            return clamp_text("⚠️ " + "; ".join(problems), self.cfg.max_len)
        return "🚀 No upcoming events in range"

    def cmd_status(self) -> str:
        now = self.now_fn()
        parts = [f"🟢 OLA {OLA_VERSION}", f"mode {self.cfg.mode}"]
        lu = self.store.load("ll2_upcoming.json")
        if lu:
            age = FetchResult(None, _parse_iso(lu["fetched"]) or now, True, False).age_label(now)
            parts.append(f"LL2 {age} ago")
        else:
            parts.append("LL2 no cache")
        gp_ages = []
        try:
            for f in sorted(os.listdir(self.store.dir)):
                if f.startswith("gp_") and f.endswith(".json"):
                    rec = self.store.load(f)
                    if rec:
                        gp_ages.append(_parse_iso(rec["fetched"]) or now)
        except OSError:
            pass
        if gp_ages:
            parts.append(f"GP {len(gp_ages)} sets, newest {FetchResult(None, max(gp_ages), True, False).age_label(now)} ago")
        else:
            parts.append("GP no cache")
        for prov in ("launchlibrary2", "celestrak"):
            if self.store.in_backoff(prov, now.timestamp()):
                parts.append(f"{'LL2' if prov == 'launchlibrary2' else 'GP'} backoff")
        parts.append("sgp4 ok" if HAVE_SGP4 else "no sgp4")
        parts.append("loc ok" if self.cfg.has_location else "no loc")
        parts.append(f"ledger {len(self.store.ledger)}")
        return fit_parts(parts, self.cfg.max_len)

    # -- notification cycle ------------------------------------------------

    def cycle(self) -> List[Alert]:
        """One polling cycle: returns new alerts (already recorded in the ledger)."""
        cfg = self.cfg
        now = self.now_fn()
        alerts: List[Alert] = []
        if cfg.mode in ("launches", "orbit_and_launch"):
            try:
                upcoming, _ = self.launches("upcoming")
                previous: List[Launch] = []
                if cfg.alert_outcomes or cfg.alert_cancellations:
                    try:
                        previous, _ = self.launches("previous")
                    except ProviderError as e:
                        _log(f"previous launches unavailable: {e}")
                alerts.extend(launch_alerts(cfg, self.store, upcoming, previous, now))
            except ProviderError as e:
                _log(f"launch cycle skipped: {e}")
        if cfg.mode in ("satellites", "iss", "orbit_and_launch") and HAVE_SGP4 and cfg.has_location:
            ids, groups = self._satellite_targets()
            if cfg.mode == "orbit_and_launch" and not ids and not groups:
                ids = [ISS_NORAD]
            recs, _provs, _errs = self.gp_records(ids=ids, groups=groups)
            try:
                passes, _ = self.passes(recs, hours=max(1.0, (cfg.pass_alert_minutes + 30) / 60.0))
            except ProviderError as e:
                _log(f"pass cycle skipped: {e}")
                passes = []
            for p in passes:
                lead = (p.aos - now).total_seconds() / 60.0
                if not (0 <= lead <= cfg.pass_alert_minutes):
                    continue
                if cfg.alert_iss_visible_only and p.norad == ISS_NORAD and not p.visible:
                    continue
                key = f"pass:{p.norad}:{p.aos.strftime('%Y%m%dT%H%M')}"
                if self.store.was_sent(key):
                    continue
                if p.visible:
                    typ, msg = "visible_pass", fmt_pass(cfg, p, now, visible_style=True)
                elif p.norad == ISS_NORAD:
                    typ, msg = "iss_pass", fmt_pass(cfg, p, now)
                else:
                    typ, msg = "satellite_pass", fmt_pass(cfg, p, now)
                alerts.append(Alert(typ, msg, key, p.to_dict()))
                self.store.mark_sent(key, now.timestamp())
        kept: List[Alert] = []
        for a in alerts:
            if not self.store.suppressed(a.message, now.timestamp(), cfg.suppress_minutes):
                kept.append(a)
        return kept


# ---------------------------------------------------------------------------
# Demo mode (simulated, offline, clearly labelled)
# ---------------------------------------------------------------------------

def demo_lines(cfg: Config, now: Optional[datetime] = None, compact: bool = True) -> List[str]:
    """Simulated alerts covering every category. Never real data."""
    now = now or _utcnow()
    t_iss = now + timedelta(hours=3, minutes=12)
    t_noaa = now + timedelta(minutes=47)

    def lt(dt: datetime) -> str:
        return fmt_local(cfg, dt, now)

    lines = [
        "🧪 DEMO 🚀 FALCON 9 | KSC | T-1H",
        "🧪 DEMO 🚀 STARSHIP | T-15M | TX",
        "🧪 DEMO ⚠️ LAUNCH DELAYED | FALCON 9",
        "🧪 DEMO ✅ LAUNCH SUCCESS | ELECTRON",
        f"🧪 DEMO 🛰️ ISS | {lt(t_iss)} | MAX 65° | 6MIN",
        f"🧪 DEMO 🛰️ NOAA 19 | AOS {lt(t_noaa)} | 48°",
        f"🧪 DEMO 🌌 ISS VISIBLE | {lt(t_iss)} | 6MIN",
        "🧪 DEMO 🚀 No launches in next 24h",
    ]
    if compact:
        return lines
    return ["🟢 Orbit and Launch Alert demo started (SIMULATED data, not live)"] + lines + ["✅ Demo complete"]


def demo_response(cfg: Config, arg: str, now: Optional[datetime] = None) -> str:
    """Command responses while OLA_MODE=demo (simulated)."""
    now = now or _utcnow()
    lines = demo_lines(cfg, now)
    pick = {
        "": lines[0], "launch": lines[0], "launches": "🧪 DEMO 🚀 2 in 24h: FALCON 9 KSC | STARSHIP TX",
        "iss": lines[4], "satellites": lines[5], "sats": lines[5], "sat": lines[4], "visible": lines[6],
    }
    return clamp_text(pick.get(arg, lines[0]), cfg.max_len)


# ---------------------------------------------------------------------------
# MeshMonitor command handling
# ---------------------------------------------------------------------------

COMMANDS = ("", "launch", "launches", "iss", "satellites", "sats", "sat", "visible", "status", "demo", "help")


def parse_command(message: str) -> Tuple[str, List[str]]:
    """Return ("ola", [args...]) for `!ola ...` / `/ola ...`, else ("", [])."""
    parts = (message or "").strip().split()
    if not parts or parts[0].lower() not in ("!ola", "/ola"):
        return ("", [])
    return ("ola", [p.lower() for p in parts[1:]])


def help_text(compact: bool = True) -> str:
    if compact:
        return "!ola [launch|launches|iss|satellites|sat <NORAD>|visible|status|demo|help]"
    return (
        f"Orbit and Launch Alert {OLA_VERSION}\n"
        "\n"
        "Providers:\n"
        "  launches   -> The Space Devs Launch Library 2 (OLA_LAUNCH_PROVIDER=launchlibrary2)\n"
        "  satellites -> CelesTrak GP + local SGP4 (OLA_SATELLITE_PROVIDER=celestrak)\n"
        "\n"
        "MeshMonitor commands:\n"
        "  !ola             next relevant space event\n"
        "  !ola launch      next upcoming rocket launch\n"
        "  !ola launches    launches in the next 24h\n"
        "  !ola iss         next ISS overpass\n"
        "  !ola satellites  upcoming satellite passes\n"
        "  !ola sat 25544   passes for one NORAD ID\n"
        "  !ola visible     predicted visible passes\n"
        "  !ola status      data source and cache status\n"
        "  !ola demo        simulated sample alerts\n"
        "  !ola help        this help\n"
    )


def handle_command(engine: Engine, args: List[str]) -> str:
    """Dispatch one `!ola` command to a single response string."""
    cfg = engine.cfg
    arg = args[0] if args else ""
    if arg in ("help", "h", "?"):
        return help_text(True)
    if arg == "demo":
        lines = demo_lines(cfg)
        return clamp_text(" | ".join([lines[0], lines[4].replace("🧪 DEMO ", "")]), cfg.max_len)
    if arg not in COMMANDS:
        return clamp_text(f"❓ Unknown '{arg[:20]}' | try !ola help", cfg.max_len)
    if arg == "sat":
        if len(args) < 2:
            return "⚠️ Usage: !ola sat <NORAD ID>"
        try:
            norad = parse_norad_id(args[1])
        except ConfigError:
            return clamp_text(f"⚠️ Invalid NORAD ID '{args[1][:12]}'", cfg.max_len)
        if cfg.mode == "demo":
            return clamp_text(f"🧪 DEMO 🛰️ #{norad} | AOS 14:22 | 48° (simulated)", cfg.max_len)
        return engine.cmd_sat(norad)
    if arg == "status":
        return engine.cmd_status()
    if cfg.mode == "demo":
        return demo_response(cfg, arg)
    if arg == "launch":
        return engine.cmd_launch()
    if arg == "launches":
        return engine.cmd_launches()
    if arg == "iss":
        return engine.cmd_iss()
    if arg in ("satellites", "sats"):
        return engine.cmd_satellites()
    if arg == "visible":
        return engine.cmd_visible()
    return engine.cmd_next()


# ---------------------------------------------------------------------------
# Output: JSON (MeshMonitor), stdout (standalone), MQTT (optional)
# ---------------------------------------------------------------------------

def mm_out(response_text: str, max_len: int = MESHMONITOR_MAX_LEN, stream: Any = None) -> None:
    """Print exactly one MeshMonitor JSON object."""
    stream = stream or sys.stdout
    stream.write(json.dumps({"response": clamp_text(response_text, max_len)}, ensure_ascii=False) + "\n")
    stream.flush()


def mm_out_many(lines: List[str], max_len: int = MESHMONITOR_MAX_LEN, stream: Any = None) -> None:
    """Timer Trigger output: {"response": ""} when nothing is due (MeshMonitor
    sends nothing), else {"responses": [...]} (one mesh packet each)."""
    stream = stream or sys.stdout
    lines = [clamp_text(l, max_len) for l in lines if l and l.strip()]
    if len(lines) > MAX_TIMER_MESSAGES:
        extra = len(lines) - (MAX_TIMER_MESSAGES - 1)
        lines = lines[: MAX_TIMER_MESSAGES - 1] + [f"➕ {extra} more alerts | !ola launches / !ola satellites"]
    if not lines:
        payload: Dict[str, Any] = {"response": ""}
    elif len(lines) == 1:
        payload = {"response": lines[0]}
    else:
        payload = {"responses": lines}
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def running_in_meshmonitor(env: Optional[Dict[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return env.get("MESSAGE") is not None or env.get("TIMER_NAME") is not None


def mqtt_enabled(cfg: Config, env: Optional[Dict[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    if not cfg.mqtt_host:
        return False
    if running_in_meshmonitor(env) and not cfg.mqtt_allow_in_meshmonitor:
        return False
    # Never republish something that itself arrived over an MQTT bridge.
    if str(env.get("VIA_MQTT", "")).lower() == "true":
        return False
    return True


def build_event(cfg: Config, event_type: str, message: str, data: Optional[Dict[str, Any]] = None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    return {
        "source": OLA_SOURCE,
        "version": OLA_VERSION,
        "ts": _iso(now or _utcnow()),
        "type": event_type,
        "mode": cfg.mode,
        "geo": {"lat": cfg.lat, "lon": cfg.lon},
        "message": message,
        "data": data or {},
    }


def mqtt_publish(cfg: Config, event: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> bool:
    """Publish via mosquitto_pub (same approach as Sky and Sea Alert)."""
    if not mqtt_enabled(cfg, env):
        return False
    mosq = shutil.which("mosquitto_pub")
    if not mosq:
        if not running_in_meshmonitor(env):
            _status("⚠️ MQTT enabled but mosquitto_pub not found; skipping MQTT publish")
        return False
    cmd = [mosq, "-h", cfg.mqtt_host, "-p", str(cfg.mqtt_port), "-t", cfg.mqtt_topic,
           "-m", json.dumps(event, ensure_ascii=False)]
    if cfg.mqtt_username:
        cmd += ["-u", cfg.mqtt_username]
    if cfg.mqtt_password:
        cmd += ["-P", cfg.mqtt_password]
    if cfg.mqtt_tls:
        cmd += ["--tls-version", "tlsv1.2"]
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return True
    except Exception:
        return False


def emit(cfg: Config, event_type: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    if not running_in_meshmonitor():
        print(message, flush=True)
    mqtt_publish(cfg, build_event(cfg, event_type, message, data))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_meshmonitor_command(message: str, env: Optional[Dict[str, str]] = None, engine: Optional[Engine] = None,
                            stream: Any = None) -> int:
    cfg = engine.cfg if engine else load_config(env)
    cmd, args = parse_command(message)
    if cmd != "ola":
        mm_out("", stream=stream)
        return 0
    if cfg.errors and (not args or args[0] not in ("help", "demo")):
        mm_out(f"⚠️ Config: {cfg.errors[0]}", cfg.max_len, stream)
        return 0
    try:
        engine = engine or Engine(cfg, in_meshmonitor=True)
        text = handle_command(engine, args)
        try:
            engine.store.flush()
        except OSError as e:
            _log(f"state write failed: {e}")
    except Exception as e:  # never leak a traceback onto stdout
        _log(f"command failed: {e!r}")
        text = "⚠️ Orbit and Launch Alert error (see MeshMonitor logs)"
        print(f"orbit_and_launch_alert: {e!r}", file=sys.stderr)
    mm_out(text or "⏸ No data", cfg.max_len, stream)
    return 0


def run_timer(env: Optional[Dict[str, str]] = None, engine: Optional[Engine] = None, stream: Any = None) -> int:
    """MeshMonitor Timer Trigger: one notification cycle, JSON out."""
    cfg = engine.cfg if engine else load_config(env)
    if cfg.errors:
        print(f"orbit_and_launch_alert config: {'; '.join(cfg.errors)}", file=sys.stderr)
        mm_out_many([], stream=stream)
        return 0
    if cfg.mode == "demo":
        mm_out_many([demo_lines(cfg)[0]], cfg.max_len, stream)
        return 0
    try:
        engine = engine or Engine(cfg, in_meshmonitor=True)
        alerts = engine.cycle()
        engine.store.flush()
        for a in alerts:
            mqtt_publish(cfg, build_event(cfg, a.type, a.message, a.data), env)
        mm_out_many([a.message for a in alerts], cfg.max_len, stream)
    except Exception as e:
        print(f"orbit_and_launch_alert: {e!r}", file=sys.stderr)
        mm_out_many([], stream=stream)
    return 0


def run_standalone(argv: List[str]) -> int:
    cfg = load_config()
    once = "--once" in argv
    _status(f"🟢 Orbit and Launch Alert started ({OLA_VERSION})")
    if cfg.errors:
        for e in cfg.errors:
            _status(f"⚠️ Config error: {e}")
        return 2
    _status(f"Mode: {cfg.mode}")
    _status(f"Location: {cfg.lat}, {cfg.lon} ({cfg.location_source})" if cfg.has_location else "Location: (not set)")
    _status(f"Timezone: {cfg.tz_name}")
    _status(f"Launch provider: {cfg.launch_provider} ({cfg.ll2_base})")
    _status(f"Satellite provider: {cfg.satellite_provider} (groups: {','.join(cfg.satellite_groups) or '-'}; "
            f"ids: {','.join(map(str, cfg.satellite_ids)) or '-'})")
    _status(f"SGP4: {'available' if HAVE_SGP4 else 'NOT installed (pip install sgp4)'}")
    _status(f"Cache: {cfg.cache_dir}")
    _status(f"MQTT: {'enabled' if mqtt_enabled(cfg) else 'disabled'}")

    if cfg.mode == "demo":
        for line in demo_lines(cfg, compact=False):
            emit(cfg, "demo", line)
        return 0

    if cfg.mode in ("satellites", "iss", "orbit_and_launch"):
        if not HAVE_SGP4:
            _status("⚠️ sgp4 not installed; satellite predictions disabled")
        if not cfg.has_location:
            _status("⚠️ OLA_LAT/OLA_LON not set; satellite predictions disabled")

    engine = Engine(cfg)
    _status(f"Polling every {cfg.poll_interval}s (launch data refresh {cfg.launch_refresh_min}m)")
    while True:
        _status("🔄 Checking launches and passes…")
        for a in engine.cycle():
            emit(cfg, a.type, a.message, a.data)
        try:
            engine.store.flush()
        except OSError as e:
            _status(f"⚠️ Could not write state: {e}")
        if once:
            return 0
        time.sleep(cfg.poll_interval)


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print(f"Orbit and Launch Alert {OLA_VERSION}")
        return 0
    if "--help" in argv or "-h" in argv:
        print(help_text(False))
        return 0
    msg = os.getenv("MESSAGE")
    if msg is not None:
        return run_meshmonitor_command(msg)
    if os.getenv("TIMER_NAME") is not None:
        return run_timer()
    return run_standalone(argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if not running_in_meshmonitor():
            _status("⏹ Stopped")
        sys.exit(0)
