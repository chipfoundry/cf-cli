"""Tests for remote precheck git consistency checks."""

import subprocess
from pathlib import Path

import pytest

from chipfoundry_cli.remote_precheck_git import RemotePrecheckGitError, verify_remote_precheck_repo


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _init_digital_project(work: Path) -> None:
    (work / "gds").mkdir(parents=True)
    (work / "gds" / "user_project_wrapper.gds").write_bytes(b"\x00")
    (work / "verilog" / "rtl").mkdir(parents=True)
    (work / "verilog" / "rtl" / "user_defines.v").write_text("//gpio\n")
    cf = work / ".cf"
    cf.mkdir()
    (cf / "project.json").write_text('{"project":{"type":"digital"}}')


def test_verify_passes_when_head_matches_origin_main(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    _init_digital_project(work)
    _git(work, "add", ".")
    _git(work, "commit", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")

    verify_remote_precheck_repo(work, "main", checks=(), skip_checks=())


def test_verify_fails_when_local_ahead_of_origin(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    _init_digital_project(work)
    _git(work, "add", ".")
    _git(work, "commit", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")

    (work / "README.md").write_text("x")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "second")

    with pytest.raises(RemotePrecheckGitError, match="must match origin"):
        verify_remote_precheck_repo(work, "main", checks=(), skip_checks=())


def test_verify_fails_dirty_user_defines(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    _init_digital_project(work)
    _git(work, "add", ".")
    _git(work, "commit", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")

    ud = work / "verilog" / "rtl" / "user_defines.v"
    ud.write_text("//changed\n")

    with pytest.raises(RemotePrecheckGitError, match="user_defines.v"):
        verify_remote_precheck_repo(work, "main", checks=(), skip_checks=())


def test_verify_skips_user_defines_when_only_xor(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(bare), str(work)], check=True, capture_output=True)
    _init_digital_project(work)
    _git(work, "add", ".")
    _git(work, "commit", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")

    ud = work / "verilog" / "rtl" / "user_defines.v"
    ud.write_text("//changed\n")

    verify_remote_precheck_repo(work, "main", checks=("xor",), skip_checks=())
