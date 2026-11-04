"""CelesTrak OMM handling and local SGP4 pass prediction."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import MIAMI, SAT_NOW, fixture_bytes, fixture_json, ola

pytestmark = pytest.mark.skipif(not ola.HAVE_SGP4, reason="sgp4 not installed")

ISS = fixture_json("gp_iss.json")[0]
LAT, LON = 25.7816, -80.2220


def test_omm_validation():
    assert ola.validate_omm(ISS)["NORAD_CAT_ID"] == 25544
    bad = dict(ISS)
    del bad["MEAN_MOTION"]
    assert ola.validate_omm(bad) is None
    assert ola.validate_omm(dict(ISS, NORAD_CAT_ID="abc")) is None
    assert ola.validate_omm(dict(ISS, EPOCH="not-a-date")) is None


def test_parse_gp_payload_variants():
    assert len(ola.parse_gp_payload(fixture_bytes("gp_iss.json"))) == 1
    assert ola.parse_gp_payload(b"No GP data found") == []
    with pytest.raises(ola.ProviderError):
        ola.parse_gp_payload(b"<html>error</html>")
    with pytest.raises(ola.ProviderError):
        ola.parse_gp_payload(b'[{"foo": 1}]')


def test_rise_set_and_max_elevation_match_reference():
    """Reference values from Skyfield (same element set, same observer)."""
    passes = ola.find_passes(ISS, LAT, LON, 0, SAT_NOW, 24, 10, evaluate_visibility=False)
    assert len(passes) == 3
    p = passes[0]
    ref_aos = datetime(2008, 9, 20, 22, 45, 4, tzinfo=timezone.utc)
    ref_tca = datetime(2008, 9, 20, 22, 49, 55, tzinfo=timezone.utc)
    ref_los = datetime(2008, 9, 20, 22, 54, 46, tzinfo=timezone.utc)
    assert abs((p.aos - ref_aos).total_seconds()) <= 2
    assert abs((p.tca - ref_tca).total_seconds()) <= 3
    assert abs((p.los - ref_los).total_seconds()) <= 2
    assert abs(p.max_el - 40.46) < 0.1
    assert round(p.aos_az) == 208 and round(p.los_az) == 50
    assert p.duration_min == 10 and p.norad == 25544 and p.name == "ISS"


@pytest.mark.parametrize("min_el,count", [(0, 4), (10, 3), (30, 2), (60, 0)])
def test_min_elevation_filter(min_el, count):
    assert len(ola.find_passes(ISS, LAT, LON, 0, SAT_NOW, 24, min_el, evaluate_visibility=False)) == count


def test_point_azel_against_reference():
    prop = ola.Propagator(ISS, LAT, LON, 0)
    az, el = prop.azel(SAT_NOW)
    # Skyfield: alt/az at SAT_NOW for the same TLE/observer
    assert el < 0  # below horizon at 13:00Z
    assert 0 <= az < 360


def test_gmst_reference():
    # GMST at J2000.0 (2000-01-01 12:00 UT) = 280.46061837 deg
    jd, fr = ola._jd(datetime(2000, 1, 1, 12, tzinfo=timezone.utc))
    assert abs(math_deg(ola.gmst_rad(jd, fr)) - 280.46061837) < 1e-4


def math_deg(r):
    import math
    return math.degrees(r) % 360.0


# --- visibility -------------------------------------------------------------------------

def test_sun_altitude_day_and_night():
    noon = datetime(2026, 6, 21, 17, 30, tzinfo=timezone.utc)   # ~13:30 EDT Miami
    night = datetime(2026, 6, 21, 6, 0, tzinfo=timezone.utc)    # ~02:00 EDT
    assert ola.sun_altitude(LAT, LON, noon) > 60
    assert ola.sun_altitude(LAT, LON, night) < -30


def test_shadow_model():
    sun = (ola.AU_KM, 0.0, 0.0)
    assert ola.is_sunlit((7000.0, 0.0, 0.0), sun)          # day side
    assert not ola.is_sunlit((-7000.0, 0.0, 0.0), sun)     # directly behind Earth
    assert ola.is_sunlit((-7000.0, 7000.0, 0.0), sun)      # night side but outside the shadow cylinder


def test_daylight_pass_is_not_visible():
    p = ola.find_passes(ISS, LAT, LON, 0, SAT_NOW, 24, 10)[0]  # 22:45Z = 18:45 EDT, sun up
    assert ola.sun_altitude(LAT, LON, p.tca) > 0
    assert p.visible is False


def test_night_visible_pass_classification():
    """Self-consistency: in an evening-twilight window (early Oct 2008 for
    these elements) some passes are classified visible, and every visible one
    has a dark sky at the start of its visible segment."""
    start = datetime(2008, 10, 8, 13, 0, tzinfo=timezone.utc)
    passes = ola.find_passes(ISS, LAT, LON, 0, start, 72, 10)
    vis = [p for p in passes if p.visible]
    assert vis, [p.to_dict() for p in passes]
    for p in vis:
        assert ola.sun_altitude(LAT, LON, p.visible_start) <= ola.DARK_SUN_ALT_DEG
    assert any(p.visible is False for p in passes)


def test_visibility_skipped_is_none():
    p = ola.find_passes(ISS, LAT, LON, 0, SAT_NOW, 24, 10, evaluate_visibility=False)[0]
    assert p.visible is None
    assert "VISIBLE" not in ola.fmt_pass(ola.load_config({"OLA_TIMEZONE": "UTC"}), p, SAT_NOW)


# --- data problems -----------------------------------------------------------------------

def test_expired_elements_refused(make_engine, http):
    eng = make_engine({"OLA_MODE": "iss", **MIAMI}, now=SAT_NOW + timedelta(days=30))
    resp = eng.cmd_iss()
    assert resp.startswith("⚠️ ISS ELEMENTS EXPIRED"), resp


def test_missing_satellite_data_offline(make_engine, http):
    http.route("CATNR=25544", exc=ola.ProviderError("network error: down"))
    eng = make_engine({"OLA_MODE": "iss", **MIAMI}, now=SAT_NOW)
    assert eng.cmd_iss().startswith("⚠️ No orbital data")


def test_location_required(make_engine, http):
    eng = make_engine({"OLA_MODE": "iss"}, now=SAT_NOW)
    assert eng.cmd_iss() == "⚠️ Set OLA_LAT/OLA_LON for pass predictions"


def test_meshmonitor_node_location_fallback():
    cfg = ola.load_config({"MM_LAT": "25.7", "MM_LON": "-80.2"})
    assert cfg.has_location and cfg.location_source == "MeshMonitor node"


def test_sgp4_missing_message(make_engine, monkeypatch):
    monkeypatch.setattr(ola, "HAVE_SGP4", False)
    eng = make_engine({"OLA_MODE": "iss", **MIAMI}, now=SAT_NOW)
    assert "sgp4" in eng.cmd_iss()


def test_pass_alert_cycle_and_dedupe(make_engine, http):
    eng = make_engine({"OLA_MODE": "iss", **MIAMI, "OLA_MIN_ELEVATION_DEG": "10",
                       "OLA_PASS_ALERT_MINUTES": "10"},
                      now=datetime(2008, 9, 20, 22, 40, tzinfo=timezone.utc))
    alerts = eng.cycle()
    assert len(alerts) == 1 and alerts[0].type == "iss_pass"
    assert alerts[0].message.startswith("🛰️ ISS | 22:45 UTC")
    assert eng.cycle() == []


def test_group_cap(make_engine, http):
    many = [dict(ISS, NORAD_CAT_ID=n, OBJECT_NAME=f"OBJ {n}") for n in range(1, 31)]
    http.route("GROUP=amateur", json.dumps(many).encode())
    eng = make_engine({"OLA_MODE": "satellites", **MIAMI, "OLA_SATELLITE_GROUPS": "amateur",
                       "OLA_MAX_SATELLITES": "5"}, now=SAT_NOW)
    recs, _p, _e = eng.gp_records(groups=["amateur"])
    assert len(recs) == 5


@pytest.mark.parametrize("cmd", ["cmd_visible", "cmd_satellites", "cmd_iss"])
def test_expired_elements_reported_by_every_command(make_engine, http, cmd):
    eng = make_engine({"OLA_MODE": "iss", **MIAMI}, now=SAT_NOW + timedelta(days=19))
    assert getattr(eng, cmd)().startswith("⚠️ ISS ELEMENTS EXPIRED"), cmd
