"""Launch Library 2 parsing, filters, thresholds, changes, outcomes, dedupe."""

import copy
import json
from datetime import timedelta

import pytest

from conftest import LAUNCH_NOW, fixture_json, ola

FALCON = "00000000-test-0000-0000-000000000001"


def launches_from(payload):
    return [ola.launch_from_dict(d) for d in ola.parse_ll2_page(payload)]


def upcoming():
    return launches_from(fixture_json("ll2_upcoming.json"))


def previous():
    return launches_from(fixture_json("ll2_previous.json"))


def cfg_with(**env):
    base = {"OLA_TIMEZONE": "UTC"}
    base.update({k: str(v) for k, v in env.items()})
    cfg = ola.load_config(base)
    assert not cfg.errors, cfg.errors
    return cfg


# --- parsing / schema ---------------------------------------------------------------

def test_parse_skips_invalid_records():
    ls = upcoming()
    assert len(ls) == 4
    assert all(l.id != "broken-record-missing-net" for l in ls)


def test_parse_fields():
    f = next(l for l in upcoming() if l.id == FALCON)
    assert f.rocket == "Falcon 9" and f.provider == "SpaceX" and f.status_id == ola.LL2_GO
    assert f.mission == "Test Starlink Group 99-1" and "USA" in f.country.split(",")
    assert f.url == "https://example.com/mission"


@pytest.mark.parametrize("payload", [{}, {"results": "x"}, [], {"results": [{"bad": 1}]}])
def test_schema_validation_rejects(payload):
    with pytest.raises(ola.ProviderError):
        ola.parse_ll2_page(payload)


def test_ll2_220_shape_supported():
    raw = {"id": "x", "name": "Falcon 9 | Y", "net": "2026-11-05T13:00:00Z", "status": {"id": 1},
           "pad": {"name": "SLC-40", "location": {"name": "Cape Canaveral, FL, USA", "country_code": "USA"}}}
    l = ola.launch_from_dict(ola.normalize_launch(raw))
    assert l.country == "USA" and l.rocket == "Falcon 9"


# --- filters ----------------------------------------------------------------------

@pytest.mark.parametrize("env,expected", [
    ({}, 4),
    ({"OLA_LAUNCH_COUNTRIES": "USA"}, 3),
    ({"OLA_LAUNCH_COUNTRIES": "nz"}, 1),
    ({"OLA_LAUNCH_AGENCIES": "spacex"}, 2),
    ({"OLA_LAUNCH_AGENCIES": "ULA,rocket lab"}, 2),
    ({"OLA_LAUNCH_SITES": "cape"}, 2),
    ({"OLA_LAUNCH_SITES": "starbase"}, 1),
    ({"OLA_LAUNCH_ROCKETS": "falcon"}, 1),
    ({"OLA_LAUNCH_MISSIONS": "starlink"}, 1),
    ({"OLA_LAUNCH_AGENCIES": "spacex", "OLA_LAUNCH_SITES": "cape"}, 1),
])
def test_filters(env, expected):
    cfg = cfg_with(**env)
    assert sum(ola.launch_matches(cfg, l) for l in upcoming()) == expected


def test_distance_is_labelled_as_distance_only():
    cfg = cfg_with(OLA_LAT=25.7816, OLA_LON=-80.2220)
    f = next(l for l in upcoming() if l.id == FALCON)
    d = ola.fmt_distance(cfg, f)
    assert d.endswith("MI AWAY") and 150 < int(d.split("MI")[0]) < 250
    assert "VISIBLE" not in ola.fmt_launch_line(cfg, f, LAUNCH_NOW)


# --- thresholds ---------------------------------------------------------------------

def run_alerts(store, cfg, now, ups=None, prev=None):
    return ola.launch_alerts(cfg, store, ups if ups is not None else upcoming(),
                             prev if prev is not None else [], now)


def test_threshold_tightest_only_no_burst(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    # Falcon at 13:00; now 12:00 -> T-24H and T-1H both due; only T-1H is sent.
    alerts = [a for a in run_alerts(store, cfg, LAUNCH_NOW) if a.data.get("id") == FALCON]
    assert [a.data["threshold_min"] for a in alerts] == [60]
    assert alerts[0].message.startswith("🚀 FALCON 9 | CCSFS | T-1H")
    assert store.ledger[f"launch:{FALCON}:T1440"]["skipped"] is True


@pytest.mark.parametrize("minutes_before,expected", [(1500, None), (1430, 1440), (59, 60), (14, 15), (0, 0)])
def test_threshold_math(tmp_path, minutes_before, expected):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    f = next(l for l in upcoming() if l.id == FALCON)
    now = f.net - timedelta(minutes=minutes_before)
    got = [a.data["threshold_min"] for a in run_alerts(store, cfg, now, [f]) if a.type == "launch_alert"]
    assert got == ([] if expected is None else [expected])


def test_t0_is_scheduled_not_confirmed(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    f = next(l for l in upcoming() if l.id == FALCON)
    a = run_alerts(store, cfg, f.net + timedelta(minutes=1), [f])[0]
    assert "SCHEDULED T-0" in a.message and "NOT YET CONFIRMED" in a.message
    assert "SUCCESS" not in a.message and "LIFTOFF CONFIRMED" not in a.message


def test_configurable_thresholds(tmp_path):
    cfg = cfg_with(OLA_LAUNCH_ALERT_HOURS="2", OLA_LAUNCH_ALERT_MINUTES="30")
    assert cfg.alert_thresholds_min == [120, 30]
    store = ola.Store(str(tmp_path))
    f = next(l for l in upcoming() if l.id == FALCON)
    assert run_alerts(store, cfg, f.net - timedelta(minutes=59), [f])[0].data["threshold_min"] == 120


def test_no_duplicate_notifications(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    first = run_alerts(store, cfg, LAUNCH_NOW)
    assert first
    assert run_alerts(store, cfg, LAUNCH_NOW + timedelta(minutes=5)) == []


def test_restart_persistence(tmp_path):
    cfg = cfg_with()
    s1 = ola.Store(str(tmp_path))
    assert run_alerts(s1, cfg, LAUNCH_NOW)
    s1.flush()
    s2 = ola.Store(str(tmp_path))  # simulated restart
    assert run_alerts(s2, cfg, LAUNCH_NOW + timedelta(minutes=1)) == []


# --- delays / reschedules / holds / cancellations ----------------------------------

def shifted(payload, launch_id, **changes):
    p = copy.deepcopy(payload)
    for r in p["results"]:
        if r.get("id") == launch_id:
            r.update(changes)
    return p


def test_delay_rearms_thresholds_and_alerts_once(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    assert f"launch:{FALCON}:T60" in store.ledger
    delayed = launches_from(shifted(fixture_json("ll2_upcoming.json"), FALCON, net="2026-11-06T12:00:00Z"))
    alerts = run_alerts(store, cfg, LAUNCH_NOW + timedelta(minutes=10), delayed)
    msgs = [a.message for a in alerts]
    assert any(m.startswith("⚠️ LAUNCH DELAYED | FALCON 9") for m in msgs), msgs
    assert f"launch:{FALCON}:T60" not in store.ledger  # re-armed for the new NET
    # T-24H fires again for the new NET (one alert), and the delay is not repeated.
    assert any(a.data.get("threshold_min") == 1440 for a in alerts)
    assert run_alerts(store, cfg, LAUNCH_NOW + timedelta(minutes=20), delayed) == []
    # Other launches' ledger entries were untouched.
    assert any(k.startswith("launch:00000000-test-0000-0000-000000000004:") for k in store.ledger)


def test_earlier_net_is_rescheduled(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    earlier = launches_from(shifted(fixture_json("ll2_upcoming.json"), "00000000-test-0000-0000-000000000003",
                                    net="2026-11-19T12:00:00Z"))
    msgs = [a.message for a in run_alerts(store, cfg, LAUNCH_NOW, earlier)]
    assert any(m.startswith("🔁 LAUNCH RESCHEDULED | STARSHIP") for m in msgs)


def test_hold_alert(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    held = launches_from(shifted(fixture_json("ll2_upcoming.json"), FALCON,
                                 status={"id": 5, "name": "On Hold", "abbrev": "Hold"}))
    msgs = [a.message for a in run_alerts(store, cfg, LAUNCH_NOW, held)]
    assert any(m.startswith("⏸️ LAUNCH HOLD | FALCON 9") for m in msgs)


def test_disable_delay_alerts(tmp_path):
    cfg, store = cfg_with(OLA_ALERT_DELAYS="false"), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    delayed = launches_from(shifted(fixture_json("ll2_upcoming.json"), FALCON, net="2026-11-06T13:00:00Z"))
    assert not any("DELAYED" in a.message for a in run_alerts(store, cfg, LAUNCH_NOW, delayed))


def test_cancellation_status(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    canceled = launches_from(shifted(fixture_json("ll2_upcoming.json"), "00000000-test-0000-0000-000000000003",
                                     status={"id": 99, "name": "Launch Canceled", "abbrev": "Canceled"}))
    msgs = [a.message for a in run_alerts(store, cfg, LAUNCH_NOW, canceled)]
    assert any(m.startswith("❌ LAUNCH CANCELED | STARSHIP") for m in msgs)


def test_removed_from_schedule_is_not_called_canceled(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    payload = fixture_json("ll2_upcoming.json")
    # Vulcan (Nov 5 20:15Z) is inside the window the list still covers (up to Nov 20).
    payload["results"] = [r for r in payload["results"] if r.get("id") != "00000000-test-0000-0000-000000000004"]
    alerts = run_alerts(store, cfg, LAUNCH_NOW, launches_from(payload))
    msgs = [a.message for a in alerts]
    assert any(m.startswith("⚠️ LAUNCH REMOVED FROM SCHEDULE | VULCAN VC4S") for m in msgs), msgs
    assert not any("CANCELED" in m for m in msgs)


def test_in_flight_reported_only_from_source(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    flying = launches_from(shifted(fixture_json("ll2_upcoming.json"), FALCON,
                                   status={"id": 6, "name": "Launch in Flight", "abbrev": "In Flight"}))
    msgs = [a.message for a in run_alerts(store, cfg, LAUNCH_NOW + timedelta(hours=1, minutes=2), flying)]
    assert any(m.startswith("🚀 LIFTOFF CONFIRMED | FALCON 9") for m in msgs)


def test_outcomes_only_when_source_confirms(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    alerts = run_alerts(store, cfg, LAUNCH_NOW, [], previous())
    msgs = sorted(a.message for a in alerts)
    assert any(m.startswith("✅ LAUNCH SUCCESS | ELECTRON") for m in msgs)
    assert any(m.startswith("❌ LAUNCH FAILURE | LONG MARCH 2D") for m in msgs)
    assert run_alerts(store, cfg, LAUNCH_NOW, [], previous()) == []
    # A Go launch whose NET has passed is never declared a success.
    f = next(l for l in upcoming() if l.id == FALCON)
    later = run_alerts(ola.Store(str(tmp_path / "x")), cfg, f.net + timedelta(hours=2), [f], [])
    assert not any("SUCCESS" in a.message for a in later)


def test_outcomes_respect_filters(tmp_path):
    cfg, store = cfg_with(OLA_LAUNCH_COUNTRIES="USA"), ola.Store(str(tmp_path))
    assert run_alerts(store, cfg, LAUNCH_NOW, [], previous()) == []


def test_old_outcomes_not_announced(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    # Success at 06:00Z and failure at 02:00Z: 14h and 18h before 20:00Z, outside the 12h window.
    alerts = run_alerts(store, cfg, LAUNCH_NOW + timedelta(hours=8), [], previous())
    assert alerts == []


def test_filter_change_does_not_fake_removal(tmp_path):
    store = ola.Store(str(tmp_path))
    run_alerts(store, cfg_with(), LAUNCH_NOW)
    narrowed = cfg_with(OLA_LAUNCH_ROCKETS="falcon")
    assert not any("REMOVED" in a.message for a in run_alerts(store, narrowed, LAUNCH_NOW))


def test_launch_beyond_list_window_not_called_removed(tmp_path):
    cfg, store = cfg_with(), ola.Store(str(tmp_path))
    run_alerts(store, cfg, LAUNCH_NOW)
    payload = fixture_json("ll2_upcoming.json")
    # Starship (Nov 20) is the latest launch; a shorter page no longer reaches it.
    payload["results"] = [r for r in payload["results"] if r.get("id") != "00000000-test-0000-0000-000000000003"]
    payload["results"] = [r for r in payload["results"] if r.get("id") != "00000000-test-0000-0000-000000000002"]
    alerts = run_alerts(store, cfg, LAUNCH_NOW, launches_from(payload))
    assert not any("STARSHIP" in a.message for a in alerts)
