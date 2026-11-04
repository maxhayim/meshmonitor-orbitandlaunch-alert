"""Caching, offline behaviour, rate limits/backoff, atomic writes and MQTT gating."""

import json
import os
from datetime import timedelta

import pytest

from conftest import LAUNCH_NOW, fixture_bytes, ola


def test_cache_hit_avoids_network(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches"})
    eng.cmd_launch()
    eng.clock["now"] = LAUNCH_NOW + timedelta(minutes=5)
    eng.cmd_launch()
    assert sum("/launches/upcoming/" in u for u in http.calls) == 1


def test_refresh_after_ttl(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches", "OLA_LAUNCH_REFRESH_MINUTES": "15"})
    eng.cmd_launch()
    eng.clock["now"] = LAUNCH_NOW + timedelta(minutes=16)
    eng.cmd_launch()
    assert sum("/launches/upcoming/" in u for u in http.calls) == 2


def test_offline_uses_cache_and_labels_it(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches"})
    eng.cmd_launch()
    http.route("/launches/upcoming/", exc=ola.ProviderError("network error: offline"))
    eng.clock["now"] = LAUNCH_NOW + timedelta(minutes=30)
    resp = eng.cmd_launch()
    assert resp.startswith("🚀 FALCON 9") and resp.endswith("CACHED 30M")


def test_stale_cache_beyond_max_age_is_not_used(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches", "OLA_LAUNCH_MAX_AGE_HOURS": "2"})
    eng.cmd_launch()
    http.route("/launches/upcoming/", exc=ola.ProviderError("network error: offline"))
    eng.clock["now"] = LAUNCH_NOW + timedelta(hours=3)
    assert eng.cmd_launch().startswith("⚠️ Launch data unavailable")


def test_api_failure_without_cache(make_engine, http):
    http.route("/launches/upcoming/", exc=ola.ProviderError("HTTP 503", 503))
    assert make_engine({"OLA_MODE": "launches"}).cmd_launch() == "⚠️ Launch data unavailable (HTTP 503)"


def test_rate_limit_sets_backoff_and_stops_calls(make_engine, http):
    http.route("/launches/upcoming/", exc=ola.RateLimited("HTTP 429 rate limited", 900))
    eng = make_engine({"OLA_MODE": "launches"})
    assert "rate limited" in eng.cmd_launch()
    n = len(http.calls)
    eng.clock["now"] = LAUNCH_NOW + timedelta(minutes=5)
    eng.cmd_launch()
    assert len(http.calls) == n  # still backing off, no new request
    assert "LL2 backoff" in eng.cmd_status()


def test_exponential_backoff(tmp_path):
    s = ola.Store(str(tmp_path))
    s.record_failure("x", 0)
    s.record_failure("x", 0)
    s.record_failure("x", 0)
    assert s.backoff["x"]["until"] == 240
    for _ in range(20):
        s.record_failure("x", 0)
    assert s.backoff["x"]["until"] == 3600
    s.record_success("x")
    assert not s.in_backoff("x", 1)


def test_ll2_throttle_detail_is_rate_limit(make_engine, http):
    http.route("/launches/upcoming/", json.dumps({"detail": "Request was throttled. Expected available in 900 seconds."}).encode())
    assert "rate limited" in make_engine({"OLA_MODE": "launches"}).cmd_launch()


def test_celestrak_403_is_rate_limit(monkeypatch):
    def boom(url, timeout, headers=None):
        raise ola.ProviderError("HTTP 403", 403)
    monkeypatch.setattr(ola, "http_get", boom)
    with pytest.raises(ola.RateLimited):
        ola.fetch_gp(ola.load_config({}), {"CATNR": "25544"})


def test_atomic_write_and_corrupt_cache(tmp_path):
    p = tmp_path / "a.json"
    ola.atomic_write_json(str(p), {"x": 1})
    assert json.loads(p.read_text()) == {"x": 1}
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")]
    p.write_text("{corrupt")
    assert ola.read_json(str(p)) is None
    s = ola.Store(str(tmp_path))
    (tmp_path / "state.json").write_text("not json")
    assert ola.Store(str(tmp_path)).ledger == {}
    assert s.load("a.json") is None


def test_ledger_bounded(tmp_path):
    s = ola.Store(str(tmp_path))
    for i in range(ola.Store.LEDGER_MAX + 50):
        s.mark_sent(f"k{i}", 1_000_000 + i)
    s.prune(1_000_000 + 10)
    assert len(s.ledger) == ola.Store.LEDGER_MAX
    s.mark_sent("old", 0)
    s.prune(ola.Store.LEDGER_TTL_S + 10)
    assert "old" not in s.ledger


def test_gp_cache_files_bounded(tmp_path):
    s = ola.Store(str(tmp_path))
    for i in range(ola.Store.GP_FILES_MAX + 5):
        s.save(f"gp_catnr_{i}.json", [], "celestrak")
    assert len([f for f in os.listdir(tmp_path) if f.startswith("gp_")]) == ola.Store.GP_FILES_MAX


def test_suppression_window(tmp_path):
    s = ola.Store(str(tmp_path))
    assert not s.suppressed("msg", 0, 15)
    assert s.suppressed("msg", 60, 15)
    assert not s.suppressed("msg", 15 * 60 + 1, 15)


# --- MQTT ---------------------------------------------------------------------------------

def test_mqtt_disabled_by_default(monkeypatch):
    called = []
    monkeypatch.setattr(ola.subprocess, "run", lambda *a, **k: called.append(a))
    cfg = ola.load_config({})
    assert not ola.mqtt_enabled(cfg, {})
    assert ola.mqtt_publish(cfg, ola.build_event(cfg, "status", "x"), {}) is False
    assert called == []


def test_mqtt_blocked_inside_meshmonitor_unless_allowed():
    cfg = ola.load_config({"OLA_MQTT_HOST": "broker.local"})
    assert ola.mqtt_enabled(cfg, {})
    assert not ola.mqtt_enabled(cfg, {"MESSAGE": "!ola"})
    assert not ola.mqtt_enabled(cfg, {"TIMER_NAME": "ola"})
    allowed = ola.load_config({"OLA_MQTT_HOST": "broker.local", "OLA_MQTT_ALLOW_IN_MESHMONITOR": "true"})
    assert ola.mqtt_enabled(allowed, {"MESSAGE": "!ola"})


def test_mqtt_never_republishes_bridge_traffic():
    cfg = ola.load_config({"OLA_MQTT_HOST": "b", "OLA_MQTT_ALLOW_IN_MESHMONITOR": "true"})
    assert not ola.mqtt_enabled(cfg, {"MESSAGE": "!ola", "VIA_MQTT": "true"})


def test_event_schema_has_no_secrets():
    cfg = ola.load_config({"OLA_MQTT_HOST": "b", "OLA_MQTT_PASSWORD": "s3cret", "OLA_LL2_API_KEY": "tok",
                           "OLA_LAT": "25.7816", "OLA_LON": "-80.2220", "OLA_MODE": "satellites"})
    ev = ola.build_event(cfg, "satellite_pass", "🛰️ ISS | 20:15", {"norad": 25544}, LAUNCH_NOW)
    assert set(ev) == {"source", "version", "ts", "type", "mode", "geo", "message", "data"}
    assert ev["source"] == "orbit-and-launch-alert" and ev["version"] == "v1.0.0"
    assert ev["ts"] == "2026-11-05T12:00:00Z" and ev["geo"] == {"lat": 25.7816, "lon": -80.222}
    assert "s3cret" not in json.dumps(ev) and "tok" not in json.dumps(ev)
