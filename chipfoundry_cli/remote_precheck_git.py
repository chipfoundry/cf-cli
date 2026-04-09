"""
Git consistency checks before queueing remote precheck.

Ensures local HEAD matches the commit the platform will clone for --git-ref and that
working tree / index match that commit for precheck inputs (wrapper GDS, user_defines.v
when the GPIO check runs, and .cf/project.json when it is tracked).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Optional, Set, Tuple


class RemotePrecheckGitError(Exception):
    """Local repository state is not consistent with origin for remote precheck."""


_GDS_BASES: Tuple[Tuple[str, str], ...] = (
    ("analog", "gds/user_analog_project_wrapper"),
    ("digital", "gds/user_project_wrapper"),
    ("openframe", "gds/openframe_project_wrapper"),
    ("mini", "gds/user_project_wrapper_mini4"),
)
_GDS_SUFFIXES: Tuple[str, ...] = (".gds", ".gds.gz")

USER_DEFINES_REL = "verilog/rtl/user_defines.v"
CF_PROJECT_JSON_REL = ".cf/project.json"


def _run_git(repo: Path, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _resolve_origin_tip_sha(repo: Path, git_ref: str) -> str:
    ref = git_ref.strip()
    if not ref:
        raise RemotePrecheckGitError("--git-ref must be a non-empty branch or tag name.")
    r = _run_git(repo, "ls-remote", "origin", f"refs/heads/{ref}")
    if r.returncode != 0:
        raise RemotePrecheckGitError(
            f"git ls-remote failed (is 'origin' configured and reachable?): {r.stderr.strip() or r.stdout}"
        )
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    if lines:
        return lines[0].split()[0]
    r2 = _run_git(repo, "ls-remote", "origin", f"refs/tags/{ref}")
    if r2.returncode != 0:
        raise RemotePrecheckGitError(
            f"git ls-remote failed for tags: {r2.stderr.strip() or r2.stdout}"
        )
    lines2 = [ln for ln in r2.stdout.strip().splitlines() if ln.strip()]
    if lines2:
        return lines2[0].split()[0]
    raise RemotePrecheckGitError(
        f"No branch or tag {ref!r} found on origin. Push the ref or fix --git-ref."
    )


def _local_head_sha(repo: Path) -> str:
    r = _run_git(repo, "rev-parse", "HEAD")
    if r.returncode != 0:
        raise RemotePrecheckGitError(
            f"Not a valid git checkout: {r.stderr.strip() or 'git rev-parse HEAD failed'}"
        )
    return r.stdout.strip()


def _detect_wrapper_gds(repo: Path) -> Tuple[str, str]:
    """Return (project_kind, relative_path) for the single wrapper GDS, or raise."""
    hits: list[Tuple[str, str]] = []
    for kind, base in _GDS_BASES:
        for suf in _GDS_SUFFIXES:
            rel = base + suf
            if (repo / rel).is_file():
                hits.append((kind, rel))
                break
    if not hits:
        raise RemotePrecheckGitError(
            "No wrapper GDS found (expected exactly one of e.g. "
            "gds/user_project_wrapper.gds, gds/user_analog_project_wrapper.gds, …). "
            "Remote precheck requires the same layout as local cf-precheck."
        )
    if len(hits) > 1:
        paths = ", ".join(h[1] for h in hits)
        raise RemotePrecheckGitError(
            f"Multiple wrapper GDS layouts found ({paths}). Remove extras so only one project type is present."
        )
    return hits[0]


def _load_cf_project_type(project_json: Path) -> Optional[str]:
    if not project_json.is_file():
        return None
    try:
        data = json.loads(project_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    t = data.get("project", {}).get("type")
    return str(t).strip().lower() if t else None


def _gpio_defines_will_run(
    checks: Tuple[str, ...],
    skip_checks: Tuple[str, ...],
    project_kind: str,
) -> bool:
    if project_kind not in ("analog", "digital"):
        return False
    cnorm = {x.strip().lower() for x in checks if x.strip()}
    snorm = {x.strip().lower() for x in skip_checks if x.strip()}
    if "gpio_defines" in snorm:
        return False
    if cnorm:
        return "gpio_defines" in cnorm
    return True


def _path_tracked_in_git(repo: Path, rel: str) -> bool:
    r = _run_git(repo, "ls-files", "--error-unmatch", rel)
    return r.returncode == 0


def _critical_precheck_paths(
    repo: Path,
    project_json: Path,
    checks: Tuple[str, ...],
    skip_checks: Tuple[str, ...],
) -> Set[str]:
    kind_gds, gds_rel = _detect_wrapper_gds(repo)
    out: Set[str] = {gds_rel}

    cf_type = _load_cf_project_type(project_json)
    if cf_type and cf_type != kind_gds:
        raise RemotePrecheckGitError(
            f".cf/project.json type is {cf_type!r} but the wrapper GDS indicates {kind_gds!r}. "
            "Fix project type or GDS layout before remote precheck."
        )

    if _gpio_defines_will_run(checks, skip_checks, kind_gds):
        ud = repo / USER_DEFINES_REL
        if ud.is_file() or _path_tracked_in_git(repo, USER_DEFINES_REL):
            out.add(USER_DEFINES_REL)

    if _path_tracked_in_git(repo, CF_PROJECT_JSON_REL):
        out.add(CF_PROJECT_JSON_REL)

    return out


def _porcelain_paths(repo: Path) -> List[str]:
    r = _run_git(repo, "status", "--porcelain=v1", "-u")
    if r.returncode != 0:
        raise RemotePrecheckGitError(f"git status failed: {r.stderr.strip()}")
    paths: List[str] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[-1]
        if rest.startswith('"') and rest.endswith('"'):
            rest = rest[1:-1].replace('\\"', '"')
        if code == "??":
            paths.append(f"??{rest}")
        elif code.strip():
            paths.append(rest)
    return paths


def verify_remote_precheck_repo(
    project_root: Path,
    git_ref: str,
    *,
    checks: Tuple[str, ...],
    skip_checks: Tuple[str, ...],
) -> None:
    """
    Raise RemotePrecheckGitError unless origin/{git_ref} tip matches HEAD and precheck
    input paths are clean and match that revision.
    """
    repo = project_root.resolve()
    git_marker = repo / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        raise RemotePrecheckGitError(
            "Remote precheck requires a git checkout with .git (clone your GitHub repo, not a plain folder copy)."
        )

    remote_sha = _resolve_origin_tip_sha(repo, git_ref)
    head_sha = _local_head_sha(repo)
    if head_sha != remote_sha:
        raise RemotePrecheckGitError(
            f"Local HEAD ({head_sha[:7]}) must match origin {git_ref!r} ({remote_sha[:7]}). "
            f"git checkout {git_ref} && git pull, or push your commits, then retry."
        )

    project_json = repo / ".cf" / "project.json"
    critical = _critical_precheck_paths(repo, project_json, checks, skip_checks)

    dirty = _porcelain_paths(repo)
    for entry in dirty:
        if entry.startswith("??"):
            path = entry[2:]
            if path in critical:
                raise RemotePrecheckGitError(
                    f"{path!r} is untracked but required for remote precheck. "
                    "Add and commit it (or remove it) so the remote clone matches your machine."
                )
        elif entry in critical:
            raise RemotePrecheckGitError(
                f"{entry!r} has uncommitted changes. Commit or stash before remote precheck."
            )

    for rel in sorted(critical):
        r = _run_git(repo, "diff-index", "--quiet", "HEAD", "--", rel)
        if r.returncode != 0:
            raise RemotePrecheckGitError(
                f"{rel!r} has uncommitted changes. Commit or stash before remote precheck."
            )
