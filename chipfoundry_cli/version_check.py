"""Client-side upgrade check for the ``cf`` CLI.

Polls ``GET /api/v1/cli/version`` on the public API with a short timeout
and caches the response on disk so we only hit the network a few times
per day per user. Prints a dim warning if the installed version is
behind the latest published release. All errors are swallowed — a
failed check must never block or delay a normal command.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console

from chipfoundry_cli.utils import get_config_path


CACHE_FILENAME = "version_check.json"
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
NETWORK_TIMEOUT_SECONDS = 1.5
ENDPOINT_PATH = "/api/v1/cli/version"
ENV_DISABLE = "CF_SKIP_VERSION_CHECK"


@dataclass(frozen=True)
class VersionInfo:
    latest: str
    minimum_supported: str
    upgrade_command: str
    release_notes_url: str


def _cache_path() -> Path:
    return get_config_path().parent / CACHE_FILENAME


def _parse_semver(version: str) -> tuple[int, ...]:
    """Parse ``X.Y.Z`` (optionally with pre-release suffix) into a tuple.

    Unknown or non-numeric components are treated as 0 so a malformed
    version never crashes the CLI. This is intentionally lightweight —
    we don't pull in ``packaging`` just for this.
    """
    cleaned = version.strip().lstrip("v")
    # Drop any pre-release / build-metadata suffix (e.g. ``1.2.3-rc1+sha``).
    for sep in ("-", "+"):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0]
    parts: list[int] = []
    for piece in cleaned.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _is_older(current: str, target: str) -> bool:
    """Return True if ``current`` is strictly older than ``target``."""
    return _parse_semver(current) < _parse_semver(target)


def _read_cache() -> Optional[dict]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ts = data.get("fetched_at")
    if not isinstance(ts, (int, float)):
        return None
    if (time.time() - ts) > CACHE_TTL_SECONDS:
        return None
    return data


def _write_cache(payload: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f)
        tmp.replace(path)
    except OSError:
        # Cache is an optimization; never fail the command because we
        # couldn't write it.
        pass


def _fetch_latest(api_url: str, user_agent: Optional[str] = None) -> Optional[VersionInfo]:
    """Hit the platform endpoint. Returns None on any failure."""
    import httpx

    url = f"{api_url.rstrip('/')}{ENDPOINT_PATH}"
    headers = {"User-Agent": user_agent} if user_agent else {}
    try:
        resp = httpx.get(url, headers=headers, timeout=NETWORK_TIMEOUT_SECONDS)
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return None
    try:
        return VersionInfo(
            latest=str(body["latest"]),
            minimum_supported=str(body["minimum_supported"]),
            upgrade_command=str(body["upgrade_command"]),
            release_notes_url=str(body.get("release_notes_url", "")),
        )
    except (KeyError, TypeError):
        return None


def _load_or_fetch(api_url: str, user_agent: Optional[str] = None) -> Optional[VersionInfo]:
    cached = _read_cache()
    if cached is not None and "info" in cached:
        info = cached["info"]
        try:
            return VersionInfo(
                latest=str(info["latest"]),
                minimum_supported=str(info["minimum_supported"]),
                upgrade_command=str(info["upgrade_command"]),
                release_notes_url=str(info.get("release_notes_url", "")),
            )
        except (KeyError, TypeError):
            pass
    fresh = _fetch_latest(api_url, user_agent=user_agent)
    if fresh is not None:
        _write_cache(
            {
                "fetched_at": time.time(),
                "info": {
                    "latest": fresh.latest,
                    "minimum_supported": fresh.minimum_supported,
                    "upgrade_command": fresh.upgrade_command,
                    "release_notes_url": fresh.release_notes_url,
                },
            }
        )
    return fresh


def maybe_warn_outdated(
    current_version: str,
    api_url: str,
    console: Console,
    user_agent: Optional[str] = None,
) -> None:
    """Print a warning if the installed CLI is behind.

    Two severity tiers:

    * **Below ``minimum_supported``** → prominent red warning. The
      server will already reject these requests with HTTP 426 (see
      :mod:`src.cli_version_service.hard_floor`); we surface the
      upgrade instruction here so users learn *why* before they see the
      error on their next real command.
    * **Behind ``latest`` but at/above minimum** → dim yellow tip.
      Purely informational.

    Never raises.
    """
    if os.environ.get(ENV_DISABLE, "").strip() not in ("", "0", "false", "False"):
        return
    try:
        info = _load_or_fetch(api_url, user_agent=user_agent)
    except Exception:
        return
    if info is None:
        return

    notes = (
        f" ({info.release_notes_url})" if info.release_notes_url else ""
    )

    if _is_older(current_version, info.minimum_supported):
        console.print(
            f"[red]✗ cf {current_version} is below the minimum supported "
            f"version ({info.minimum_supported}).[/red] The platform will "
            f"reject API calls from this install.\n"
            f"  Upgrade now: [cyan]{info.upgrade_command}[/cyan]{notes}"
        )
        return

    if _is_older(current_version, info.latest):
        console.print(
            f"[yellow]⚠[/yellow] A newer [bold]cf[/bold] is available: "
            f"[bold]{info.latest}[/bold] (you have {current_version}).\n"
            f"  Upgrade: [cyan]{info.upgrade_command}[/cyan]{notes}",
            style="dim",
        )
