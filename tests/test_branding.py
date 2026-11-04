"""Branding and documentation consistency across the repository."""

import os
import re
import subprocess

import pytest

from conftest import ROOT, ola

TEXT_EXT = (".py", ".md", ".txt", ".example", ".yml", ".yaml", ".svg", ".cfg", ".toml", ".ini")


def repo_files():
    out = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "cache", "__pycache__", ".pytest_cache", "ola_vendor")]
        for f in files:
            if f.endswith(TEXT_EXT) or f in (".gitignore", ".env.example", "LICENSE"):
                out.append(os.path.join(dirpath, f))
    return out


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_version_constants_agree():
    head = read(os.path.join(ROOT, "orbit_and_launch_alert.py"))[:1024]
    assert "# mm_meta:" in head  # MeshMonitor reads mm_meta from the first 1KB
    assert "#   name: Orbit and Launch Alert" in head
    assert "#   version: 1.0.0" in head
    assert "#   source: maxhayim/meshmonitor-orbitandlaunch-alert/orbit_and_launch_alert.py" in head
    assert ola.OLA_VERSION == "v1.0.0"
    assert "Orbit and Launch Alert — v1.0.0" in head
    changelog = read(os.path.join(ROOT, "CHANGELOG.md"))
    assert "## [v1.0.0]" in changelog


def test_script_starts_with_shebang():
    assert read(os.path.join(ROOT, "orbit_and_launch_alert.py")).startswith("#!/usr/bin/env python3\n")


@pytest.mark.parametrize("pattern", [r"\bSSA_", r"!ssa\b", r"sky_and_sea_alert\.py", r"sky-and-sea-alert",
                                     r"SSA_VERSION", r"ADSBX_", r"AISHUB_"])
def test_no_stale_sky_and_sea_identifiers(pattern):
    hits = []
    for p in repo_files():
        if p.endswith("test_branding.py"):
            continue
        for n, line in enumerate(read(p).splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{os.path.relpath(p, ROOT)}:{n}: {line.strip()}")
    assert not hits, hits


def test_sky_and_sea_mentions_are_companion_only():
    allowed = re.compile(r"companion|Companion|Related|sister|same maintainer|family|Sky and Sea Alert\]\(|mirrors|mirroring|Mirror|like Sky and Sea|as Sky and Sea|Sky and Sea Alert's|following Sky and Sea|from Sky and Sea")
    bad = []
    for p in repo_files():
        if p.endswith("test_branding.py"):
            continue
        for n, line in enumerate(read(p).splitlines(), 1):
            if "Sky and Sea" in line or "skyandsea" in line:
                if not allowed.search(line):
                    bad.append(f"{os.path.relpath(p, ROOT)}:{n}: {line.strip()}")
    assert not bad, bad


def test_install_urls_point_to_new_repo():
    for p in repo_files():
        for url in re.findall(r"github\.com/maxhayim/[A-Za-z0-9._-]+", read(p)):
            assert re.sub(r"\.git$", "", url.rstrip(".")) in ("github.com/maxhayim/meshmonitor-orbitandlaunch-alert",
                                       "github.com/maxhayim/meshmonitor-skyandsea-alert"), (p, url)


def test_both_mesh_networks_documented():
    for name in ("README.md", "SECURITY.md", "docs/meshmonitor.md"):
        text = read(os.path.join(ROOT, name))
        assert "Meshtastic" in text and "MeshCore" in text, name


def test_readme_commands_match_parser():
    readme = read(os.path.join(ROOT, "README.md"))
    documented = set(re.findall(r"!ola(?: ([a-z]+))?", readme))
    for cmd in documented:
        assert cmd in ola.COMMANDS, cmd
    for cmd in ("launch", "launches", "iss", "satellites", "sat", "visible", "status", "demo", "help"):
        assert f"!ola {cmd}" in readme


def test_every_env_var_in_code_is_documented():
    code = read(os.path.join(ROOT, "orbit_and_launch_alert.py"))
    names = set(re.findall(r'"(OLA_[A-Z0-9_]+)"', code)) | set(re.findall(r'get(?:_\w+)?\("(OLA_[A-Z0-9_]+)"', code))
    docs = read(os.path.join(ROOT, "docs", "configuration.md"))
    example = read(os.path.join(ROOT, ".env.example"))
    missing_docs = sorted(n for n in names if n not in docs)
    missing_example = sorted(n for n in names if n not in example)
    assert not missing_docs, missing_docs
    assert not missing_example, missing_example


def test_documented_env_vars_exist_in_code():
    code = read(os.path.join(ROOT, "orbit_and_launch_alert.py"))
    docs = read(os.path.join(ROOT, "docs", "configuration.md")) + read(os.path.join(ROOT, ".env.example"))
    for name in set(re.findall(r"\bOLA_[A-Z0-9_]+\b", docs)):
        assert name in code, name


def test_internal_links_resolve():
    broken = []
    for p in repo_files():
        if not p.endswith(".md"):
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", read(p)) + re.findall(r'src="([^"]+)"', read(p)):
            if re.match(r"^[a-z]+:", target):
                continue
            full = os.path.normpath(os.path.join(os.path.dirname(p), target))
            if not os.path.exists(full):
                broken.append(f"{os.path.relpath(p, ROOT)} -> {target}")
    assert not broken, broken


def test_logo_alt_text():
    readme = read(os.path.join(ROOT, "README.md"))
    assert 'alt="Orbit and Launch Alert Logo"' in readme


def test_env_example_has_no_real_secrets():
    text = read(os.path.join(ROOT, ".env.example"))
    for line in text.splitlines():
        if re.match(r"^(OLA_MQTT_PASSWORD|OLA_LL2_API_KEY)=", line):
            assert line.split("=", 1)[1].strip() == "", line
