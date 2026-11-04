"""Shared helpers for Orbit and Launch Alert tests (offline, deterministic)."""

import json
import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)

import orbit_and_launch_alert as ola  # noqa: E402

# Fixed clocks: launches fixture is around 2026-11-05, ISS elements are from 2008-09-20.
LAUNCH_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
SAT_NOW = datetime(2008, 9, 20, 13, 0, tzinfo=timezone.utc)
MIAMI = {"OLA_LAT": "25.7816", "OLA_LON": "-80.2220"}


def fixture_bytes(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def fixture_json(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return json.load(f)


class FakeHTTP:
    """Replaces ola.http_get. Routes URLs by substring; counts calls."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def route(self, needle, body=None, exc=None):
        self.routes.insert(0, (needle, body, exc))
        return self

    def __call__(self, url, timeout, headers=None):
        self.calls.append(url)
        for needle, body, exc in self.routes:
            if needle in url:
                if exc is not None:
                    raise exc
                return 200, body
        raise ola.ProviderError("network error: no route (test)")


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    fake.route("/launches/upcoming/", fixture_bytes("ll2_upcoming.json"))
    fake.route("/launches/previous/", fixture_bytes("ll2_previous.json"))
    fake.route("CATNR=25544", fixture_bytes("gp_iss.json"))
    fake.route("GROUP=stations", fixture_bytes("gp_iss.json"))
    fake.route("CATNR=99999", b"No GP data found")
    monkeypatch.setattr(ola, "http_get", fake)
    return fake


@pytest.fixture
def make_engine(tmp_path):
    """Factory: make_engine(env_overrides, now=datetime) -> Engine with a temp cache."""

    def _make(env=None, now=LAUNCH_NOW, cache=None, allow_network=True):
        base = {"OLA_CACHE_DIR": str(cache or tmp_path / "cache"), "OLA_TIMEZONE": "UTC"}
        base.update(env or {})
        cfg = ola.load_config(base)
        assert not cfg.errors, cfg.errors
        clock = {"now": now}
        eng = ola.Engine(cfg, now_fn=lambda: clock["now"], allow_network=allow_network)
        eng.clock = clock  # tests may advance time: eng.clock["now"] = ...
        return eng

    return _make
