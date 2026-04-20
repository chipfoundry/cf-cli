"""Unit tests for the client-side version check (chipfoundry_cli.version_check)."""

import json
import time
from unittest.mock import patch

import httpx
import pytest
from rich.console import Console

from chipfoundry_cli import version_check
from chipfoundry_cli.version_check import (
    CACHE_TTL_SECONDS,
    ENV_DISABLE,
    VersionInfo,
    _is_older,
    _parse_semver,
    maybe_warn_outdated,
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Route the on-disk cache to an ephemeral tmp dir."""
    fake_config = tmp_path / "config.toml"
    monkeypatch.setattr(
        "chipfoundry_cli.version_check.get_config_path",
        lambda: fake_config,
    )
    yield tmp_path


# ── parsing / comparison ────────────────────────────────────────────────────

class TestSemverParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("1.2.3", (1, 2, 3)),
        ("v2.3.20", (2, 3, 20)),
        ("1.0", (1, 0, 0)),
        ("2", (2, 0, 0)),
        ("1.2.3-rc1", (1, 2, 3)),
        ("1.2.3+sha.abc", (1, 2, 3)),
        ("1.2.3-rc1+sha.abc", (1, 2, 3)),
        ("   2.3.20   ", (2, 3, 20)),
    ])
    def test_parses_known_forms(self, raw, expected):
        assert _parse_semver(raw) == expected

    def test_malformed_falls_back_to_zeros(self):
        assert _parse_semver("not.a.version") == (0, 0, 0)

    def test_is_older_basic(self):
        assert _is_older("2.3.19", "2.3.20") is True
        assert _is_older("2.3.20", "2.3.20") is False
        assert _is_older("2.3.21", "2.3.20") is False
        assert _is_older("1.9.9", "2.0.0") is True


# ── warning behavior ────────────────────────────────────────────────────────

class TestMaybeWarnOutdated:
    def _info(self, latest="2.3.25"):
        return VersionInfo(
            latest=latest,
            minimum_supported="2.0.0",
            upgrade_command="pip install --upgrade chipfoundry-cli",
            release_notes_url="https://example.com/notes",
        )

    def test_warns_when_outdated(self, isolated_cache):
        console = Console(record=True, force_terminal=False)
        with patch.object(version_check, "_load_or_fetch", return_value=self._info()):
            maybe_warn_outdated("2.3.19", "https://api.example.com", console)
        output = console.export_text()
        assert "newer" in output
        assert "2.3.25" in output
        assert "2.3.19" in output
        assert "pip install --upgrade chipfoundry-cli" in output

    def test_silent_when_current(self, isolated_cache):
        console = Console(record=True, force_terminal=False)
        with patch.object(version_check, "_load_or_fetch", return_value=self._info()):
            maybe_warn_outdated("2.3.25", "https://api.example.com", console)
        assert console.export_text() == ""

    def test_silent_when_ahead(self, isolated_cache):
        console = Console(record=True, force_terminal=False)
        with patch.object(version_check, "_load_or_fetch", return_value=self._info()):
            maybe_warn_outdated("9.9.9", "https://api.example.com", console)
        assert console.export_text() == ""

    def test_silent_when_fetch_returns_none(self, isolated_cache):
        console = Console(record=True, force_terminal=False)
        with patch.object(version_check, "_load_or_fetch", return_value=None):
            maybe_warn_outdated("1.0.0", "https://api.example.com", console)
        assert console.export_text() == ""

    def test_network_errors_never_surface(self, isolated_cache):
        """A transport error during fetch must be swallowed."""
        console = Console(record=True, force_terminal=False)

        def _boom(*_args, **_kwargs):
            raise httpx.ConnectError("no network")

        with patch.object(version_check.httpx, "get", _boom) if hasattr(version_check, "httpx") else patch("httpx.get", _boom):
            maybe_warn_outdated("1.0.0", "https://api.example.com", console)
        assert console.export_text() == ""

    def test_below_minimum_uses_red_blocking_message(self, isolated_cache):
        """When current < minimum_supported, surface a prominent (non-dim)
        message that makes it clear the platform will reject requests."""
        console = Console(record=True, force_terminal=False)
        info = VersionInfo(
            latest="2.4.0",
            minimum_supported="2.4.0",
            upgrade_command="pip install --upgrade chipfoundry-cli",
            release_notes_url="https://example.com/notes",
        )
        with patch.object(version_check, "_load_or_fetch", return_value=info):
            maybe_warn_outdated("2.3.19", "https://api.example.com", console)
        output = console.export_text()
        assert "below the minimum supported" in output
        assert "reject" in output
        assert "2.3.19" in output
        assert "2.4.0" in output
        # Should not also show the dim "newer available" warning.
        assert "A newer" not in output

    def test_at_minimum_shows_no_warning_when_also_latest(self, isolated_cache):
        console = Console(record=True, force_terminal=False)
        info = VersionInfo(
            latest="2.4.0",
            minimum_supported="2.4.0",
            upgrade_command="pip install --upgrade chipfoundry-cli",
            release_notes_url="",
        )
        with patch.object(version_check, "_load_or_fetch", return_value=info):
            maybe_warn_outdated("2.4.0", "https://api.example.com", console)
        assert console.export_text() == ""

    def test_at_minimum_but_behind_latest_shows_soft_tip(self, isolated_cache):
        console = Console(record=True, force_terminal=False)
        info = VersionInfo(
            latest="2.5.0",
            minimum_supported="2.4.0",
            upgrade_command="pip install --upgrade chipfoundry-cli",
            release_notes_url="",
        )
        with patch.object(version_check, "_load_or_fetch", return_value=info):
            maybe_warn_outdated("2.4.0", "https://api.example.com", console)
        output = console.export_text()
        assert "newer" in output
        assert "below the minimum" not in output

    def test_disabled_by_env_var(self, isolated_cache, monkeypatch):
        monkeypatch.setenv(ENV_DISABLE, "1")
        console = Console(record=True, force_terminal=False)
        called = {"n": 0}

        def _spy(*_a, **_kw):
            called["n"] += 1
            return self._info()

        with patch.object(version_check, "_load_or_fetch", _spy):
            maybe_warn_outdated("1.0.0", "https://api.example.com", console)
        assert called["n"] == 0
        assert console.export_text() == ""


# ── on-disk cache ───────────────────────────────────────────────────────────

class TestCache:
    def _response(self):
        return {
            "latest": "2.3.25",
            "minimum_supported": "2.0.0",
            "upgrade_command": "pip install --upgrade chipfoundry-cli",
            "release_notes_url": "https://example.com/notes",
        }

    def test_fresh_fetch_writes_cache(self, isolated_cache):
        console = Console(record=True, force_terminal=False)

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self_inner):
                return self._response()

        with patch("httpx.get", return_value=FakeResp()):
            maybe_warn_outdated("2.3.19", "https://api.example.com", console)

        cache_path = isolated_cache / "version_check.json"
        assert cache_path.exists()
        with open(cache_path) as f:
            data = json.load(f)
        assert data["info"]["latest"] == "2.3.25"
        assert isinstance(data["fetched_at"], (int, float))
        assert "2.3.25" in console.export_text()

    def test_fresh_cache_skips_network(self, isolated_cache):
        cache_path = isolated_cache / "version_check.json"
        cache_path.write_text(json.dumps({
            "fetched_at": time.time(),
            "info": self._response(),
        }))

        called = {"n": 0}

        def _boom(*_a, **_kw):
            called["n"] += 1
            raise AssertionError("network should not be hit when cache is fresh")

        console = Console(record=True, force_terminal=False)
        with patch("httpx.get", _boom):
            maybe_warn_outdated("2.3.19", "https://api.example.com", console)
        assert called["n"] == 0
        assert "2.3.25" in console.export_text()

    def test_stale_cache_triggers_refresh(self, isolated_cache):
        cache_path = isolated_cache / "version_check.json"
        cache_path.write_text(json.dumps({
            "fetched_at": time.time() - (CACHE_TTL_SECONDS + 60),
            "info": {**self._response(), "latest": "0.0.1"},
        }))

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self_inner):
                return self._response()

        console = Console(record=True, force_terminal=False)
        with patch("httpx.get", return_value=FakeResp()):
            maybe_warn_outdated("2.3.19", "https://api.example.com", console)
        # The fresh network value (2.3.25), not the stale 0.0.1, should win.
        assert "2.3.25" in console.export_text()

    def test_corrupt_cache_is_ignored(self, isolated_cache):
        cache_path = isolated_cache / "version_check.json"
        cache_path.write_text("{ not json")

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self_inner):
                return self._response()

        console = Console(record=True, force_terminal=False)
        with patch("httpx.get", return_value=FakeResp()):
            maybe_warn_outdated("2.3.19", "https://api.example.com", console)
        assert "2.3.25" in console.export_text()
