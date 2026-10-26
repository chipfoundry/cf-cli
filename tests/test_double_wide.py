"""
Tests for double-wide openframe (``double_wide``) project support.
"""
import json
import os
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

import chipfoundry_cli.main as cf_main
from chipfoundry_cli.main import main, _collect_push_candidates
from chipfoundry_cli.remote_precheck_git import _detect_wrapper_gds
from chipfoundry_cli.utils import (
    GDS_TYPE_MAP,
    HARNESS_REPOS,
    PROJECT_TYPES,
    collect_project_files,
    harness_ref,
    is_padframe_only,
)

WRAPPER = "double_wide_openframe_project_wrapper"

FAKE_VERSIONS = {
    "mpw_tags": {"sky130A": "2026.2.9", "sky130B": "2026.2.9"},
    "openlane_version": "CI2511",
    "open_pdks_commits": {"sky130A": "3e0e31dc", "sky130B": "3e0e31dc"},
}


def _write_project_json(root: Path, project_type: str = "double_wide") -> Path:
    cf_dir = root / ".cf"
    cf_dir.mkdir(parents=True, exist_ok=True)
    path = cf_dir / "project.json"
    path.write_text(json.dumps({"project": {"name": "dw", "type": project_type}}))
    return path


@pytest.fixture
def dw_project(tmp_path: Path) -> Path:
    """Initialized double-wide project with PDK, harness and wrapper GDS."""
    _write_project_json(tmp_path)
    (tmp_path / "gds").mkdir()
    (tmp_path / "gds" / f"{WRAPPER}.gds").write_bytes(b"gds")
    (tmp_path / "dependencies" / "pdks" / "sky130A").mkdir(parents=True)
    (tmp_path / "caravel" / "gds").mkdir(parents=True)
    return tmp_path


class TestUtils:
    def test_project_type_registered(self):
        assert "double_wide" in PROJECT_TYPES
        assert GDS_TYPE_MAP[f"{WRAPPER}.gds"] == "double_wide"
        assert GDS_TYPE_MAP[f"{WRAPPER}.gds.gz"] == "double_wide"

    def test_padframe_only(self):
        assert is_padframe_only("double_wide")
        assert is_padframe_only("openframe")
        assert not is_padframe_only("digital")
        assert not is_padframe_only("analog")
        assert not is_padframe_only(None)

    def test_harness_ref_defaults_to_main(self):
        assert "double_wide" in HARNESS_REPOS
        assert harness_ref("double_wide") == "main"
        assert harness_ref("double_wide", {"mpw_tags": {}}) == "main"

    def test_harness_ref_from_versions(self):
        versions = {"harness_refs": {"double_wide": "DW2026.1"}}
        assert harness_ref("double_wide", versions) == "DW2026.1"

    def test_collect_without_user_defines(self, tmp_path: Path):
        (tmp_path / "gds").mkdir()
        (tmp_path / "gds" / f"{WRAPPER}.gds.gz").write_bytes(b"gz")
        collected = collect_project_files(str(tmp_path))
        assert f"gds/{WRAPPER}.gds.gz" in collected
        assert collected["verilog/rtl/user_defines.v"] is None

    def test_collect_rejects_mixed_types(self, tmp_path: Path):
        (tmp_path / "gds").mkdir()
        (tmp_path / "gds" / f"{WRAPPER}.gds").write_bytes(b"gds")
        (tmp_path / "gds" / "openframe_project_wrapper.gds").write_bytes(b"gds")
        with pytest.raises(FileNotFoundError, match="Multiple project types"):
            collect_project_files(str(tmp_path))


class TestPushAndRemoteDetection:
    def test_push_candidates_skip_user_defines(self, tmp_path: Path):
        (tmp_path / "gds").mkdir()
        (tmp_path / "gds" / f"{WRAPPER}.gds").write_bytes(b"gds")
        (tmp_path / "verilog" / "rtl").mkdir(parents=True)
        (tmp_path / "verilog" / "rtl" / "user_defines.v").write_text("// unused")
        candidates = _collect_push_candidates(tmp_path)
        assert [c[0] for c in candidates] == [f"gds/{WRAPPER}.gds"]

    def test_remote_detects_double_wide_gds(self, tmp_path: Path):
        (tmp_path / "gds").mkdir()
        (tmp_path / "gds" / f"{WRAPPER}.gds").write_bytes(b"gds")
        assert _detect_wrapper_gds(tmp_path) == ("double_wide", f"gds/{WRAPPER}.gds")


class TestGpioConfig:
    def test_gpio_config_not_available(self, tmp_path: Path):
        _write_project_json(tmp_path)
        result = CliRunner().invoke(main, ["gpio-config", "--project-root", str(tmp_path)])
        assert result.exit_code != 0
        assert "not available for double_wide projects" in result.output


class TestSetup:
    def test_setup_installs_harness_not_mcw(self, tmp_path: Path, monkeypatch):
        _write_project_json(tmp_path)
        monkeypatch.setattr(cf_main, "fetch_versions_from_upstream", lambda *a, **k: FAKE_VERSIONS)
        result = CliRunner().invoke(
            main, ["setup", "--project-root", str(tmp_path), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "chipfoundry/double_wide_openframe" in result.output
        assert "MCW not needed for double_wide projects" in result.output
        assert "Management Core Wrapper" not in result.output
        assert "caravel-lite" not in result.output

    def test_setup_uses_pinned_harness_ref(self, tmp_path: Path, monkeypatch):
        _write_project_json(tmp_path)
        versions = dict(FAKE_VERSIONS, harness_refs={"double_wide": "DW2026.1"})
        monkeypatch.setattr(cf_main, "fetch_versions_from_upstream", lambda *a, **k: versions)
        result = CliRunner().invoke(
            main, ["setup", "--project-root", str(tmp_path), "--only-caravel", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "tag: DW2026.1" in result.output.replace("\n", "")


class TestPrecheck:
    def test_precheck_uses_harness_as_golden_root(self, dw_project: Path, monkeypatch):
        monkeypatch.setattr(cf_main.shutil, "which", lambda name: f"/usr/bin/{name}")
        result = CliRunner().invoke(
            main, ["precheck", "--project-root", str(dw_project), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        out = result.output.replace("\n", "")
        assert f"-c {(dw_project / 'caravel').resolve()}" in out
        assert "/opt/caravel" not in out
        assert "GPIO configuration not found" not in out

    def test_precheck_mounts_symlinked_harness(self, dw_project: Path, tmp_path_factory, monkeypatch):
        external = tmp_path_factory.mktemp("harness")
        (dw_project / "caravel" / "gds").rmdir()
        (dw_project / "caravel").rmdir()
        os.symlink(external, dw_project / "caravel")
        monkeypatch.setattr(cf_main.shutil, "which", lambda name: f"/usr/bin/{name}")
        result = CliRunner().invoke(
            main, ["precheck", "--project-root", str(dw_project), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        out = result.output.replace("\n", "")
        assert f"-v {external.resolve()}:{external.resolve()}" in out

    def test_precheck_requires_harness(self, dw_project: Path, monkeypatch):
        (dw_project / "caravel" / "gds").rmdir()
        (dw_project / "caravel").rmdir()
        monkeypatch.setattr(cf_main.shutil, "which", lambda name: f"/usr/bin/{name}")
        result = CliRunner().invoke(
            main, ["precheck", "--project-root", str(dw_project), "--dry-run"]
        )
        assert "harness not found" in result.output
        assert "cf setup --only-caravel" in result.output


class TestVerify:
    def test_verify_runs_in_openframe_mode(self, dw_project: Path):
        (dw_project / "verilog" / "dv" / "cocotb" / "counter").mkdir(parents=True)
        (dw_project / "verilog" / "dv" / "cocotb" / "counter" / "counter.yaml").write_text("Tests: []\n")
        bin_dir = dw_project / "venv-cocotb" / "bin"
        bin_dir.mkdir(parents=True)
        tool = bin_dir / "caravel_cocotb"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)

        result = CliRunner().invoke(
            main, ["verify", "counter", "--project-root", str(dw_project), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "GPIO configuration not found" not in result.output
        assert "-t counter -sim RTL --openframe" in result.output.replace("\n", "")
