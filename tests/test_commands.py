"""Command parsing, MeshMonitor JSON output, demo mode and invalid input."""

import io
import json
import os
import subprocess
import sys

import pytest

from conftest import LAUNCH_NOW, MIAMI, ROOT, SAT_NOW, ola

SCRIPT = os.path.join(ROOT, "orbit_and_launch_alert.py")


def run_cmd(engine, message):
    out = io.StringIO()
    ola.run_meshmonitor_command(message, engine=engine, stream=out)
    lines = out.getvalue().splitlines()
    assert len(lines) == 1, lines  # exactly one JSON object
    return json.loads(lines[0])


# --- parser -----------------------------------------------------------------

@pytest.mark.parametrize("msg,expected", [
    ("!ola", ("ola", [])),
    ("/ola", ("ola", [])),
    ("!OLA Launch", ("ola", ["launch"])),
    ("  !ola   sat   25544 ", ("ola", ["sat", "25544"])),
    ("!wx", ("", [])),
    ("hello !ola", ("", [])),
    ("", ("", [])),
])
def test_parse_command(msg, expected):
    assert ola.parse_command(msg) == expected


def test_non_ola_message_returns_empty_response(make_engine):
    assert run_cmd(make_engine(), "!wx miami") == {"response": ""}


# --- demo mode ----------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["!ola", "!ola launch", "!ola launches", "!ola iss", "!ola satellites",
                                 "!ola visible", "!ola demo", "!ola sat 25544"])
def test_demo_mode_every_command_is_labelled(make_engine, http, cmd):
    eng = make_engine({"OLA_MODE": "demo"})
    resp = run_cmd(eng, cmd)["response"]
    assert resp.startswith("🧪 DEMO"), resp
    assert http.calls == []  # demo never touches the network


def test_demo_lines_cover_all_categories():
    cfg = ola.load_config({"OLA_MODE": "demo"})
    text = "\n".join(ola.demo_lines(cfg, LAUNCH_NOW))
    for token in ("🚀", "⚠️ LAUNCH DELAYED", "✅", "🛰️ ISS", "🛰️ NOAA 19", "🌌 ISS VISIBLE", "No launches"):
        assert token in text
    assert all(l.startswith("🧪 DEMO") for l in ola.demo_lines(cfg, LAUNCH_NOW))


def test_demo_standalone_runs_offline(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "OLA_MODE": "demo", "OLA_CACHE_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "SIMULATED" in r.stdout and "🧪 DEMO" in r.stdout


# --- each live command (mocked providers) --------------------------------------

def test_help(make_engine):
    resp = run_cmd(make_engine(), "!ola help")["response"]
    for word in ("launch", "launches", "iss", "satellites", "sat", "visible", "status", "demo", "help"):
        assert word in resp


def test_launch_command(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches"})
    resp = run_cmd(eng, "!ola launch")["response"]
    assert resp.startswith("🚀 FALCON 9") and "CCSFS" in resp and "T-1H" in resp


def test_launches_command(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches"})
    resp = run_cmd(eng, "!ola launches")["response"]
    assert resp.startswith("🚀 3 in 24h:")
    assert "FALCON 9" in resp and "VULCAN" in resp and "ELECTRON" in resp


def test_next_event_launch_mode(make_engine, http):
    resp = run_cmd(make_engine({"OLA_MODE": "launches"}), "!ola")["response"]
    assert resp.startswith("🚀 FALCON 9")


def test_iss_command(make_engine, http):
    eng = make_engine({"OLA_MODE": "iss", **MIAMI, "OLA_MIN_ELEVATION_DEG": "10"}, now=SAT_NOW)
    resp = run_cmd(eng, "!ola iss")["response"]
    assert resp.startswith("🛰️ ISS | 22:45 UTC | MAX 40°"), resp


def test_satellites_command(make_engine, http):
    eng = make_engine({"OLA_MODE": "satellites", **MIAMI, "OLA_MIN_ELEVATION_DEG": "10"}, now=SAT_NOW)
    resp = run_cmd(eng, "!ola satellites")["response"]
    assert resp.startswith("🛰️ ISS 22:45 UTC 40°"), resp


def test_sat_command_valid_id(make_engine, http):
    eng = make_engine({"OLA_MODE": "satellites", **MIAMI, "OLA_MIN_ELEVATION_DEG": "30"}, now=SAT_NOW)
    resp = run_cmd(eng, "!ola sat 25544")["response"]
    assert resp.startswith("🛰️ ISS | 22:45 UTC | MAX 40°"), resp


def test_visible_command(make_engine, http):
    eng = make_engine({"OLA_MODE": "iss", **MIAMI}, now=SAT_NOW)
    resp = run_cmd(eng, "!ola visible")["response"]
    assert resp.startswith("🌌")


def test_status_command(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches"})
    run_cmd(eng, "!ola launch")
    resp = run_cmd(eng, "!ola status")["response"]
    assert resp.startswith("🟢 OLA v1.0.0") and "LL2 0M ago" in resp


# --- invalid input ---------------------------------------------------------------

def test_unknown_command(make_engine):
    resp = run_cmd(make_engine(), "!ola rocketz")["response"]
    assert resp.startswith("❓ Unknown 'rocketz'")


@pytest.mark.parametrize("arg", ["abc", "-5", "0", "1234567890", "25544x"])
def test_invalid_norad_ids(make_engine, http, arg):
    eng = make_engine({"OLA_MODE": "satellites", **MIAMI}, now=SAT_NOW)
    assert run_cmd(eng, f"!ola sat {arg}")["response"].startswith("⚠️ Invalid NORAD ID")
    assert http.calls == []


def test_sat_missing_argument(make_engine):
    assert run_cmd(make_engine({"OLA_MODE": "satellites"}), "!ola sat")["response"].startswith("⚠️ Usage")


def test_unknown_norad_id(make_engine, http):
    eng = make_engine({"OLA_MODE": "satellites", **MIAMI}, now=SAT_NOW)
    assert "not found" in run_cmd(eng, "!ola sat 99999")["response"]


def test_config_error_reported_not_silenced():
    out = io.StringIO()
    old = dict(os.environ)
    try:
        os.environ.update({"OLA_LAT": "123", "OLA_LON": "0", "OLA_MODE": "satellites"})
        ola.run_meshmonitor_command("!ola iss", stream=out)
    finally:
        os.environ.clear()
        os.environ.update(old)
    resp = json.loads(out.getvalue())["response"]
    assert resp.startswith("⚠️ Config: OLA_LAT")


def test_exceptions_never_reach_stdout(make_engine, monkeypatch, capsys):
    eng = make_engine({"OLA_MODE": "launches"})
    monkeypatch.setattr(ola, "handle_command", lambda e, a: 1 / 0)
    out = io.StringIO()
    ola.run_meshmonitor_command("!ola launch", engine=eng, stream=out)
    payload = json.loads(out.getvalue())
    assert payload["response"].startswith("⚠️")
    assert "ZeroDivisionError" in capsys.readouterr().err


# --- JSON-only subprocess contract -------------------------------------------------

@pytest.mark.parametrize("message", ["!ola help", "!ola demo", "!ola", "!ola status", "nope"])
def test_subprocess_stdout_is_single_json(tmp_path, message):
    env = {"PATH": os.environ.get("PATH", ""), "MESSAGE": message, "OLA_MODE": "demo",
           "OLA_CACHE_DIR": str(tmp_path), "OLA_DEBUG": "1"}
    r = subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert set(payload) == {"response"}
    assert len(payload["response"]) <= 200


def test_timer_trigger_outputs_json_and_dedupes(make_engine, http):
    eng = make_engine({"OLA_MODE": "launches"})
    out1, out2 = io.StringIO(), io.StringIO()
    ola.run_timer(engine=eng, stream=out1)
    first = json.loads(out1.getvalue())
    texts = first.get("responses") or [first["response"]]
    assert any(t.startswith("🚀 FALCON 9") for t in texts)
    assert all(len(t) <= 200 for t in texts)
    ola.run_timer(engine=eng, stream=out2)
    assert json.loads(out2.getvalue()) == {"response": ""}


def test_timer_overflow_is_summarised_not_dropped():
    out = io.StringIO()
    ola.mm_out_many([f"🚀 ALERT {i}" for i in range(8)], stream=out)
    payload = json.loads(out.getvalue())
    assert len(payload["responses"]) == ola.MAX_TIMER_MESSAGES
    assert payload["responses"][-1].startswith("➕ 4 more alerts")
