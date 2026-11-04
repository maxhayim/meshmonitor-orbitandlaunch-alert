"""Message formatting, length limits, timezones and configuration validation."""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import LAUNCH_NOW, fixture_json, ola


def cfg(**env):
    base = {"OLA_TIMEZONE": "UTC"}
    base.update({k: str(v) for k, v in env.items()})
    c = ola.load_config(base)
    return c


@pytest.mark.parametrize("minutes,label", [(0, "T-0"), (15, "T-15M"), (60, "T-1H"), (125, "T-2H5M"),
                                           (1440, "T-1D"), (1500, "T-1D1H")])
def test_countdown(minutes, label):
    assert ola.fmt_countdown(timedelta(minutes=minutes)) == label


@pytest.mark.parametrize("az,label", [(0, "N"), (44, "NE"), (90, "E"), (181, "S"), (300, "NW"), (359, "N")])
def test_compass(az, label):
    assert ola.compass(az) == label


def test_clamp_and_fit_parts():
    assert ola.clamp_text("x" * 250, 200) == "x" * 199 + "…"
    s = ola.fit_parts(["🚀 A", "B", "C" * 300], 50)
    assert s == "🚀 A | B"


# --- LoRa message length ---------------------------------------------------------------

def test_every_launch_line_within_limit():
    c = cfg(OLA_LAT=25.78, OLA_LON=-80.22)
    payload = fixture_json("ll2_upcoming.json")
    for d in ola.parse_ll2_page(payload):
        d["mission"] = "M" * 300
        l = ola.launch_from_dict(d)
        assert len(ola.fmt_launch_line(c, l, LAUNCH_NOW)) <= 200


@pytest.mark.parametrize("limit", ["60", "120", "200"])
def test_configurable_max_len(limit):
    c = cfg(OLA_MAX_LEN=limit)
    l = ola.launch_from_dict(ola.parse_ll2_page(fixture_json("ll2_upcoming.json"))[3])
    assert len(ola.fmt_launch_line(c, l, LAUNCH_NOW)) <= int(limit)


@pytest.mark.parametrize("bad", ["201", "500", "10", "abc"])
def test_max_len_cannot_exceed_meshmonitor(bad):
    assert any("OLA_MAX_LEN" in e for e in cfg(OLA_MAX_LEN=bad).errors)


def test_long_mission_names_are_shortened():
    l = ola.launch_from_dict(ola.parse_ll2_page(fixture_json("ll2_upcoming.json"))[3])
    m = ola.short_mission(l)
    assert m == "Test USSF-999"  # trailing parenthetical dropped
    l.mission = "A" * 80
    assert len(ola.short_mission(l)) == 24 and ola.short_mission(l).endswith("…")


def test_launch_line_shape():
    c = cfg()
    l = ola.launch_from_dict(ola.parse_ll2_page(fixture_json("ll2_upcoming.json"))[0])
    assert ola.fmt_launch_line(c, l, LAUNCH_NOW) == \
        "🚀 FALCON 9 | Test Starlink Group 99-1 | CCSFS | 13:00 UTC | T-1H | GO"


def test_tbd_precision_marked_net():
    c = cfg()
    l = ola.launch_from_dict(ola.parse_ll2_page(fixture_json("ll2_upcoming.json"))[2])
    line = ola.fmt_launch_line(c, l, LAUNCH_NOW)
    assert "NET Fri 12:00 UTC" in line and "STARBASE TX" in line and "TBD" in line


# --- timezones ---------------------------------------------------------------------------

def test_timezone_conversion_est_and_edt():
    c = cfg(OLA_TIMEZONE="America/New_York")
    winter = datetime(2026, 11, 5, 1, 15, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 5, 0, 15, tzinfo=timezone.utc)
    assert ola.fmt_local(c, winter, winter) == "20:15 EST"
    assert ola.fmt_local(c, summer, summer) == "20:15 EDT"


def test_timezone_day_prefix_when_not_today():
    c = cfg(OLA_TIMEZONE="Europe/London")
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    assert ola.fmt_local(c, now + timedelta(days=1), now) == "Fri 12:00 GMT"


def test_timezone_falls_back_to_tz_env():
    c = ola.load_config({"TZ": "America/Chicago"})
    assert c.tz_name == "America/Chicago" and not c.errors


@pytest.mark.parametrize("tzname", ["Mars/Olympus", "America/Not_A_City", "../etc/passwd"])
def test_invalid_timezone_rejected(tzname):
    assert any("OLA_TIMEZONE" in e or "timezone" in e for e in cfg(OLA_TIMEZONE=tzname).errors)


def test_internal_timestamps_are_iso_utc():
    assert ola._iso(datetime(2026, 11, 4, 7, 0, tzinfo=timezone(timedelta(hours=-5)))) == "2026-11-04T12:00:00Z"
    assert ola._parse_iso("2008-09-20T12:25:40.104192") == datetime(2008, 9, 20, 12, 25, 40, 104192, tzinfo=timezone.utc)
    assert ola._parse_iso("2026-11-05T13:00:00.12Z").microsecond == 120000


# --- coordinate / config validation --------------------------------------------------------

@pytest.mark.parametrize("lat,lon", [("91", "0"), ("-90.5", "0"), ("0", "181"), ("0", "-180.1"),
                                     ("abc", "0"), ("nan", "0"), ("inf", "0")])
def test_invalid_coordinates_rejected(lat, lon):
    c = cfg(OLA_LAT=lat, OLA_LON=lon)
    assert c.errors and not c.has_location


def test_lat_without_lon_rejected():
    assert any("both" in e for e in ola.load_config({"OLA_LAT": "25"}).errors)


@pytest.mark.parametrize("env,needle", [
    ({"OLA_MODE": "space"}, "OLA_MODE"),
    ({"OLA_SATELLITE_IDS": "25544,abc"}, "OLA_SATELLITE_IDS"),
    ({"OLA_SATELLITE_GROUPS": "stations,../x"}, "OLA_SATELLITE_GROUPS"),
    ({"OLA_LAUNCH_ALERT_HOURS": "24,x"}, "OLA_LAUNCH_ALERT_HOURS"),
    ({"OLA_LAUNCH_PROVIDER": "nextspaceflight"}, "OLA_LAUNCH_PROVIDER"),
    ({"OLA_SATELLITE_PROVIDER": "n2yo"}, "OLA_SATELLITE_PROVIDER"),
    ({"OLA_MQTT_TOPIC": "a/#"}, "OLA_MQTT_TOPIC"),
    ({"OLA_POLL_INTERVAL": "5"}, "OLA_POLL_INTERVAL"),
    ({"OLA_MQTT_TLS": "maybe"}, "OLA_MQTT_TLS"),
    ({"OLA_LAUNCH_COUNTRIES": "United States"}, "OLA_LAUNCH_COUNTRIES"),
])
def test_invalid_settings_reported(env, needle):
    assert any(needle in e for e in ola.load_config(env).errors)


def test_defaults_are_valid():
    c = ola.load_config({})
    assert not c.errors and c.mode == "demo" and c.alert_thresholds_min == [1440, 60, 15, 0]
    assert c.satellite_groups == ["stations"] and c.min_elevation_deg == 30 and c.poll_interval == 300


def test_mode_aliases():
    assert ola.load_config({"OLA_MODE": "orbit-and-launch"}).mode == "orbit_and_launch"
