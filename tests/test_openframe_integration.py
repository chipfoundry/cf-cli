"""cf harden / cf precheck integration with the openframe spec: power.json ordering and sync warnings."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from chipfoundry_cli.librelane_run import build_librelane_command, librelane_config_files
from chipfoundry_cli.main import STEP_LIST_MARKER, main
from chipfoundry_cli.openframe import render_outputs, validate_spec
from chipfoundry_cli.openframe.cli import openframe_sync_problems
from chipfoundry_cli.openframe.gpio_gen import GPIO_RTL_REL
from chipfoundry_cli.openframe.power_gen import POWER_JSON_REL

DATA = Path(__file__).parent / "data"
TIMER_SPEC = json.loads((DATA / "openframe_timer.json").read_text())
WARNING_TITLE = "Openframe spec and generated files differ"


def flat(text):
    """Undo Rich wrapping and panel borders."""
    return " ".join(text.replace("│", " ").split())


@pytest.fixture
def project(tmp_path):
    """Minimal openframe project with a wrapper and a timer macro, LibreLane venv and PDK stubs."""
    (tmp_path / ".cf").mkdir()
    (tmp_path / ".cf" / "project.json").write_text(
        json.dumps({"project": {"name": "demo", "type": "openframe", "openframe": TIMER_SPEC}})
    )
    (tmp_path / "verilog" / "rtl").mkdir(parents=True)
    wrapper = tmp_path / "openlane" / "openframe_project_wrapper"
    wrapper.mkdir(parents=True)
    (wrapper / "config.json").write_text(json.dumps({"DESIGN_NAME": "openframe_project_wrapper"}))
    timer = tmp_path / "openlane" / "user_proj_timer"
    timer.mkdir()
    (timer / "config.json").write_text(json.dumps({"DESIGN_NAME": "user_proj_timer"}))
    (tmp_path / "openlane" / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "dependencies" / "pdks" / "sky130A").mkdir(parents=True)
    return tmp_path


def generate(root):
    for rel, content in render_outputs(validate_spec(TIMER_SPEC)).items():
        (root / rel).write_text(content)


class FakeProcess:
    def __init__(self, cmd, **kwargs):
        FakeProcess.cmd = cmd
        FakeProcess.cwd = kwargs.get("cwd")
        self.pid = 0

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def fake_librelane(monkeypatch):
    """Stub everything cf harden shells out to; the LibreLane command lands in FakeProcess.cmd."""
    FakeProcess.cmd = None
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["docker", "info"] or cmd[:3] == ["nix", "flake", "metadata"]:
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        if len(cmd) > 1 and cmd[1] == "-c":  # step list query through the venv python
            out = STEP_LIST_MARKER + json.dumps({"steps": ["Yosys.Synthesis"]}) + "\n\x1b[?25h"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("chipfoundry_cli.main.subprocess.run", fake_run)
    monkeypatch.setattr("chipfoundry_cli.main.subprocess.Popen", FakeProcess)
    monkeypatch.setattr(
        "chipfoundry_cli.main.fetch_versions_from_upstream", lambda *a, **k: {"openlane_version": "CI2609"}
    )
    real_which = __import__("shutil").which
    monkeypatch.setattr(
        "chipfoundry_cli.main.shutil.which", lambda name: "/usr/bin/nix" if name == "nix" else real_which(name)
    )
    return FakeProcess


def harden(root, *args):
    return CliRunner().invoke(main, ["harden", *args, "--project-root", str(root)], catch_exceptions=False)


class TestConfigFiles:
    def test_power_json_goes_first(self, project):
        wrapper = project / "openlane" / "openframe_project_wrapper"
        assert librelane_config_files(wrapper / "config.json") == [wrapper / "config.json"]
        generate(project)
        assert librelane_config_files(wrapper / "config.json") == [wrapper / "power.json", wrapper / "config.json"]

    def test_power_json_is_per_macro(self, project):
        generate(project)
        timer = project / "openlane" / "user_proj_timer" / "config.json"
        assert librelane_config_files(timer) == [timer]

    @pytest.mark.parametrize("backend", ["nix", "docker"])
    def test_build_librelane_command(self, project, backend):
        generate(project)
        wrapper = project / "openlane" / "openframe_project_wrapper"
        cmd, _ = build_librelane_command(
            project_root=project, flow_root=project / "openlane", config_file=wrapper / "config.json",
            pdk="sky130A", pdk_root=project / "dependencies" / "pdks", tag="t", openlane_version="CI2609",
            backend=backend,
        )
        assert cmd[-2:] == [str(wrapper / "power.json"), str(wrapper / "config.json")]


class TestHardenCommand:
    @pytest.mark.parametrize("flag,first", [("--use-docker", "python3"), ("--use-nix", "nix")])
    def test_wrapper_passes_power_json_before_config(self, project, fake_librelane, flag, first):
        generate(project)
        res = harden(project, "openframe_project_wrapper", flag)
        assert res.exit_code == 0, res.output
        wrapper = project / "openlane" / "openframe_project_wrapper"
        cmd = fake_librelane.cmd
        assert Path(cmd[0]).name == first
        assert cmd[-2:] == [str(wrapper / "power.json"), str(wrapper / "config.json")]
        assert cmd.count(str(wrapper / "config.json")) == 1
        assert "power.json + config.json" in res.output
        assert WARNING_TITLE not in res.output

    def test_macro_without_power_json(self, project, fake_librelane):
        generate(project)
        res = harden(project, "user_proj_timer", "--use-docker")
        assert res.exit_code == 0, res.output
        timer = project / "openlane" / "user_proj_timer"
        assert fake_librelane.cmd[-1] == str(timer / "config.json")
        assert str(timer / "power.json") not in fake_librelane.cmd

    def test_warns_when_generated_files_missing(self, project, fake_librelane):
        res = harden(project, "openframe_project_wrapper", "--use-docker")
        out = flat(res.output)
        assert WARNING_TITLE in out
        assert f"{GPIO_RTL_REL}: missing" in out and f"{POWER_JSON_REL}: missing" in out
        assert "cf openframe generate" in out
        assert "cf harden continues with the files currently on disk" in out
        assert res.exit_code == 0

    def test_timer_macro_does_not_warn(self, project, fake_librelane):
        res = harden(project, "user_proj_timer", "--use-docker")
        assert WARNING_TITLE not in flat(res.output)

    def test_list_does_not_warn(self, project):
        res = harden(project, "--list")
        assert WARNING_TITLE not in flat(res.output)


class TestSyncProblems:
    def test_in_sync(self, project):
        generate(project)
        assert openframe_sync_problems(project) == []

    def test_spec_changed_after_generate(self, project):
        generate(project)
        pj = project / ".cf" / "project.json"
        data = json.loads(pj.read_text())
        data["project"]["openframe"]["gpio"]["pads"][0]["mode"] = "input_pd"
        pj.write_text(json.dumps(data))
        assert openframe_sync_problems(project) == [
            f"{GPIO_RTL_REL}: generated from a different spec",
            f"{POWER_JSON_REL}: generated from a different spec",
        ]

    def test_hand_edited(self, project):
        generate(project)
        rtl = project / GPIO_RTL_REL
        rtl.write_text(rtl.read_text().replace(".MODE(3'd3)", ".MODE(3'd2)"))
        assert openframe_sync_problems(project) == [f"{GPIO_RTL_REL}: edited by hand since it was generated"]

    def test_foreign_file(self, project):
        generate(project)
        (project / POWER_JSON_REL).write_text(json.dumps({"VDD_NETS": ["vccd1"]}))
        assert openframe_sync_problems(project) == [
            f"{POWER_JSON_REL}: not generated by cf openframe generate (no spec-sha256 header)"
        ]

    def test_invalid_spec(self, project):
        pj = project / ".cf" / "project.json"
        data = json.loads(pj.read_text())
        data["project"]["openframe"]["gpio"]["pads"][3]["mode"] = "input"
        pj.write_text(json.dumps(data))
        problems = openframe_sync_problems(project)
        assert problems[0] == "project.openframe is invalid, so the generated files cannot be checked:"
        assert any("mixes directions" in p for p in problems[1:])

    def test_missing_block(self, project):
        (project / ".cf" / "project.json").write_text(json.dumps({"project": {"type": "openframe"}}))
        problems = openframe_sync_problems(project)
        assert len(problems) == 1 and "has no project.openframe spec" in problems[0]

    def test_not_openframe(self, project):
        (project / ".cf" / "project.json").write_text(json.dumps({"project": {"type": "digital"}}))
        assert openframe_sync_problems(project) == []


class TestPrecheckWarning:
    def test_precheck_warns_when_stale(self, project):
        generate(project)
        pj = project / ".cf" / "project.json"
        data = json.loads(pj.read_text())
        data["project"]["openframe"]["power"]["domains"] = ["vccd1", "vccd2"]
        pj.write_text(json.dumps(data))
        res = CliRunner().invoke(main, ["precheck", "--project-root", str(project), "--dry-run"])
        out = flat(res.output)
        assert WARNING_TITLE in out
        assert f"{POWER_JSON_REL}: generated from a different spec" in out
        assert "cf precheck continues" in out

    def test_precheck_quiet_when_in_sync(self, project):
        generate(project)
        res = CliRunner().invoke(main, ["precheck", "--project-root", str(project), "--dry-run"])
        assert WARNING_TITLE not in flat(res.output)
