"""Tests for the openframe spec validator, generators, CLI commands and TUI."""

import asyncio
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from chipfoundry_cli.main import main
from chipfoundry_cli.openframe import SpecError, read_spec_file, render_outputs, validate_spec, write_spec_file
from chipfoundry_cli.openframe.gpio_gen import GPIO_RTL_REL, generate_gpio_rtl
from chipfoundry_cli.openframe.power_gen import POWER_JSON_REL, power_config

DATA = Path(__file__).parent / "data"
TIMER_SPEC = json.loads((DATA / "openframe_timer.json").read_text())
CF_GPIO_CONFIG_RTL = DATA / "CF_gpio_config.v"


def timer_spec():
    return copy.deepcopy(TIMER_SPEC)


def errors_of(data):
    with pytest.raises(SpecError) as exc:
        validate_spec(data)
    return exc.value.errors


def assert_error(data, pattern):
    errors = errors_of(data)
    assert any(re.search(pattern, e) for e in errors), errors


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class TestValidator:
    def test_timer_spec_is_valid(self):
        spec = validate_spec(timer_spec())
        assert [(s.name, s.direction, s.width) for s in spec.signals] == [
            ("clk", "in", None),
            ("rst", "in", None),
            ("out", "out", 11),
        ]
        assert spec.signals[2].pads == tuple(range(2, 13))
        assert len(spec.pads) == 44
        assert all(p.unused and p.mode == "analog" for p in spec.pads[13:])
        assert spec.domains == ("vccd1",)
        assert spec.macros[0].gnd_net == "vssd1"

    def test_full_listing_without_unused_mode(self):
        data = timer_spec()
        del data["gpio"]["unused_mode"]
        data["gpio"]["pads"] += [{"pad": n, "mode": "analog"} for n in range(13, 44)]
        spec = validate_spec(data)
        assert not any(p.unused for p in spec.pads)

    def test_missing_pads_without_unused_mode(self):
        data = timer_spec()
        del data["gpio"]["unused_mode"]
        assert_error(data, r"pads 13-43 are not configured")

    def test_duplicate_pad(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 0, "mode": "input", "signal": "other"})
        assert_error(data, r"pad 0 is listed more than once")

    def test_bus_gap(self):
        data = timer_spec()
        data["gpio"]["pads"] = [p for p in data["gpio"]["pads"] if p.get("bit") != 4]
        assert_error(data, r"signal 'out\[10:0\]' has no pad for bit\(s\) 4")

    def test_duplicate_bit(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "output", "signal": "out", "bit": 3})
        assert_error(data, r"bit 3 is assigned to both pad 5 and pad 20")

    def test_input_mode_on_output_bus(self):
        data = timer_spec()
        data["gpio"]["pads"][5]["mode"] = "input"
        assert_error(data, r"signal 'out' mixes directions")

    def test_bus_mixing_bit_and_scalar(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "output", "signal": "out"})
        assert_error(data, r"signal 'out': pads 20 need a 'bit'")

    def test_scalar_signal_on_two_pads(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "input", "signal": "clk"})
        assert_error(data, r"signal 'clk' is used by pads 0, 20 without bit indices")

    def test_output_requires_signal(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "output"})
        assert_error(data, r"pad 20: mode 'output' requires a signal")

    def test_analog_rejects_signal(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "analog", "signal": "vin"})
        assert_error(data, r"pad 20: analog pads cannot have a signal")

    def test_bit_without_signal(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "input", "bit": 0})
        assert_error(data, r"'bit' is set but 'signal' is missing")

    def test_input_without_signal_is_allowed(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "input_pd"})
        spec = validate_spec(data)
        assert spec.pads[20].mode == "input_pd" and not spec.pads[20].unused

    @pytest.mark.parametrize("name,pattern", [
        ("gpio_in", "clashes with an openframe_project_wrapper port"),
        ("analog_io", "clashes with an openframe_project_wrapper port"),
        ("wire", "is a Verilog keyword"),
    ])
    def test_reserved_signal_names(self, name, pattern):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "input", "signal": name})
        assert_error(data, pattern)

    def test_bidir_port_collision(self):
        data = timer_spec()
        data["gpio"]["pads"] += [
            {"pad": 20, "mode": "bidir", "signal": "sda"},
            {"pad": 21, "mode": "input", "signal": "sda_in"},
        ]
        assert_error(data, r"signal 'sda' creates port 'sda_in', which is already used by signal 'sda_in'|"
                           r"signal 'sda_in' creates port 'sda_in', which is already used by signal 'sda'")

    def test_bidir_port_clashes_with_pad_port(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 20, "mode": "bidir", "signal": "gpio"})
        assert_error(data, r"signal 'gpio' port 'gpio_in' clashes with an openframe_project_wrapper port")

    def test_macro_domain_not_enabled(self):
        data = timer_spec()
        data["power"]["macros"][0]["domain"] = "vccd2"
        assert_error(data, r"macro 'mprj' uses domain 'vccd2', which is not in power.domains")

    def test_duplicate_macro(self):
        data = timer_spec()
        data["power"]["macros"].append(dict(data["power"]["macros"][0]))
        assert_error(data, r"macro instance 'mprj' is listed more than once")

    @pytest.mark.parametrize("mutate,pattern", [
        (lambda d: d.update(schema_version=2), r"schema_version: 1 was expected"),
        (lambda d: d["gpio"]["pads"][0].update(mode="inout"), r"gpio/pads/0/mode: 'inout' is not one of"),
        (lambda d: d["gpio"]["pads"][0].update(pad=44), r"gpio/pads/0/pad: 44 is greater than the maximum"),
        (lambda d: d["gpio"]["pads"][0].update(overrides={"slow": 2}), r"overrides/slow: 2 is not one of"),
        (lambda d: d["gpio"]["pads"][0].update(overrides={"slow": True}), r"overrides/slow: True is not of type"),
        (lambda d: d["gpio"]["pads"][0].update(overrides={"fast": 1}), r"Additional properties are not allowed"),
        (lambda d: d["gpio"]["pads"][0].update(signal="1bad"), r"gpio/pads/0/signal: '1bad' does not match"),
        (lambda d: d["gpio"].update(unused_mode="output"), r"gpio/unused_mode: 'output' is not one of"),
        (lambda d: d.pop("power"), r"'power' is a required property"),
        (lambda d: d["power"].update(domains=[]), r"power/domains: \[\] should be non-empty"),
        (lambda d: d["power"].update(domains=["vccd1", "vccd1"]), r"has non-unique elements"),
        (lambda d: d["power"].update(domains=["vddio"]), r"'vddio' is not one of"),
        (lambda d: d["power"]["macros"][0].pop("vdd_pin"), r"'vdd_pin' is a required property"),
        (lambda d: d.update(extra=1), r"Additional properties are not allowed \('extra'"),
    ])
    def test_schema_errors(self, mutate, pattern):
        data = timer_spec()
        mutate(data)
        assert_error(data, pattern)

    def test_errors_are_collected(self):
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 0, "mode": "input"})
        data["gpio"]["pads"].append({"pad": 20, "mode": "output"})
        data["power"]["macros"][0]["domain"] = "vdda1"
        assert len(errors_of(data)) == 3

    def test_normalized_and_hash_stable(self):
        data = timer_spec()
        shuffled = timer_spec()
        shuffled["gpio"]["pads"].reverse()
        assert validate_spec(data).raw == validate_spec(shuffled).raw
        assert validate_spec(data).sha256 == validate_spec(shuffled).sha256
        data["gpio"]["pads"][0]["mode"] = "input_pd"
        assert validate_spec(data).sha256 != validate_spec(shuffled).sha256


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

class TestSpecFiles:
    @pytest.mark.parametrize("name", ["spec.json", "spec.yaml", "spec.yml"])
    def test_round_trip(self, tmp_path, name):
        path = tmp_path / name
        write_spec_file(path, timer_spec())
        assert validate_spec(read_spec_file(path)).raw == validate_spec(timer_spec()).raw

    def test_wrapped_block_is_accepted(self, tmp_path):
        path = tmp_path / "spec.json"
        path.write_text(json.dumps({"openframe": timer_spec()}))
        validate_spec(read_spec_file(path))

    def test_duplicate_json_key(self, tmp_path):
        path = tmp_path / "spec.json"
        path.write_text('{"schema_version": 1, "schema_version": 1}')
        with pytest.raises(SpecError, match="duplicate key 'schema_version'"):
            read_spec_file(path)

    def test_duplicate_yaml_key(self, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_text("schema_version: 1\nschema_version: 1\n")
        with pytest.raises(SpecError, match="duplicate key 'schema_version'"):
            read_spec_file(path)

    def test_unknown_extension(self, tmp_path):
        path = tmp_path / "spec.txt"
        path.write_text("{}")
        with pytest.raises(SpecError, match="cannot infer format"):
            read_spec_file(path)
        assert read_spec_file(path, "json") == {}

    def test_parse_error(self, tmp_path):
        path = tmp_path / "spec.json"
        path.write_text("{not json")
        with pytest.raises(SpecError, match="cannot parse json"):
            read_spec_file(path)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def _instance_params(rtl):
    """pad index -> parameter text of its CF_gpio_config instance."""
    found = re.findall(r"CF_gpio_config #\(\n(.*?)\n    \) gpio_cfg_(\d\d) \(", rtl, re.S)
    return {int(n): " ".join(p.split()) for p, n in found}


class TestGpioGenerator:
    def test_timer_matches_golden(self):
        rtl = generate_gpio_rtl(validate_spec(timer_spec()))
        assert rtl == (DATA / "openframe_gpio_timer.v").read_text()

    def test_timer_pinout(self):
        rtl = generate_gpio_rtl(validate_spec(timer_spec()))
        params = _instance_params(rtl)
        assert len(params) == 44
        assert params[0] == ".MODE(3'd1)"
        assert params[1] == ".MODE(3'd3)"
        assert all(params[n] == ".MODE(3'd4)" for n in range(2, 13))
        assert all(params[n] == ".MODE(3'd0)" for n in range(13, 44))
        assert ".io_in            (clk)" in rtl
        assert ".io_in            (rst)" in rtl
        for i in range(11):
            assert f".io_out           (out[{i}])," in rtl
        assert "input  wire [10:0]  out," in rtl
        assert "CF_gpio_config v1.2.0" not in rtl

    def test_overrides_and_bidir(self):
        data = timer_spec()
        data["gpio"]["unused_mode"] = "input_pd"
        data["gpio"]["pads"] += [
            {"pad": 20, "mode": "bidir", "signal": "sda", "overrides": {"slow": 1, "vtrip": 1}},
            {"pad": 21, "mode": "bidir", "signal": "io", "bit": 1},
            {"pad": 22, "mode": "bidir", "signal": "io", "bit": 0, "overrides": {"holdover": 1}},
        ]
        rtl = generate_gpio_rtl(validate_spec(data))
        params = _instance_params(rtl)
        assert params[20] == ".MODE(3'd5), .SLOW(1'b1), .VTRIP(1'b1)"
        assert params[22] == ".MODE(3'd5), .HOLDOVER(1'b1)"
        assert params[30] == ".MODE(3'd2)"
        assert "requires CF_gpio_config v1.2.0" in rtl
        for port in ("output wire         sda_in,", "input  wire         sda_out,", "input  wire         sda_oeb,",
                     "output wire [1:0]   io_in,", "input  wire [1:0]   io_out,", "input  wire [1:0]   io_oeb,"):
            assert port in rtl
        assert ".io_oeb           (io_oeb[0])," in rtl

    def test_no_signals(self):
        data = {"schema_version": 1, "gpio": {"unused_mode": "analog", "pads": []},
                "power": {"domains": ["vccd1"], "macros": []}}
        rtl = generate_gpio_rtl(validate_spec(data))
        assert "// User design ports" not in rtl
        assert len(_instance_params(rtl)) == 44


class TestPowerGenerator:
    def test_timer_power(self):
        cfg = power_config(validate_spec(timer_spec()))
        assert cfg["VDD_NETS"] == ["vccd1"]
        assert cfg["GND_NETS"] == ["vssd1"]
        assert cfg["PDN_MACRO_CONNECTIONS"] == ["mprj vccd1 vssd1 vccd1 vssd1"]

    def test_multi_domain_keeps_order(self):
        data = timer_spec()
        data["power"] = {
            "domains": ["vccd2", "vccd1", "vdda1"],
            "macros": [
                {"instance": "mprj", "domain": "vccd1", "vdd_pin": "vccd1", "gnd_pin": "vssd1"},
                {"instance": "core2/u_ana", "domain": "vdda1", "vdd_pin": "VDD", "gnd_pin": "VSS"},
            ],
        }
        cfg = power_config(validate_spec(data))
        assert cfg["VDD_NETS"] == ["vccd2", "vccd1", "vdda1"]
        assert cfg["GND_NETS"] == ["vssd2", "vssd1", "vssa1"]
        assert cfg["PDN_MACRO_CONNECTIONS"] == ["mprj vccd1 vssd1 vccd1 vssd1", "core2/u_ana vdda1 vssa1 VDD VSS"]


LINT_SPECS = {
    "timer": timer_spec(),
    "mixed": {
        "schema_version": 1,
        "gpio": {"unused_mode": "input_pu", "pads": [
            {"pad": 0, "mode": "input", "signal": "clk"},
            {"pad": 5, "mode": "bidir", "signal": "sda", "overrides": {"slow": 1, "analog_en": 1}},
            {"pad": 6, "mode": "bidir", "signal": "gp", "bit": 0},
            {"pad": 7, "mode": "bidir", "signal": "gp", "bit": 1},
            {"pad": 8, "mode": "output", "signal": "led"},
            {"pad": 9, "mode": "input_pd"},
            {"pad": 10, "mode": "analog", "overrides": {"analog_sel": 1, "analog_pol": 1}},
        ]},
        "power": {"domains": ["vccd1", "vccd2"], "macros": []},
    },
}


@pytest.mark.parametrize("name", sorted(LINT_SPECS))
class TestGeneratedRtlLints:
    def _write(self, tmp_path, name):
        path = tmp_path / "openframe_gpio.v"
        path.write_text(generate_gpio_rtl(validate_spec(LINT_SPECS[name])))
        return path

    @pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not installed")
    def test_verilator(self, tmp_path, name):
        rtl = self._write(tmp_path, name)
        res = subprocess.run(
            ["verilator", "--lint-only", "-Wall", "--top-module", "openframe_gpio", str(rtl), str(CF_GPIO_CONFIG_RTL)],
            capture_output=True, text=True,
        )
        assert res.returncode == 0, res.stderr

    @pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")
    def test_iverilog(self, tmp_path, name):
        rtl = self._write(tmp_path, name)
        res = subprocess.run(
            ["iverilog", "-g2005", "-Wall", "-Wno-timescale", "-o", str(tmp_path / "a.out"), str(rtl),
             str(CF_GPIO_CONFIG_RTL)],
            capture_output=True, text=True,
        )
        assert res.returncode == 0, res.stderr
        assert "warning" not in res.stderr.lower(), res.stderr


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def openframe_project(tmp_path):
    (tmp_path / ".cf").mkdir()
    (tmp_path / "verilog" / "rtl").mkdir(parents=True)
    (tmp_path / "openlane" / "openframe_project_wrapper").mkdir(parents=True)
    project = {"project": {"name": "demo", "type": "openframe", "openframe": timer_spec()}}
    (tmp_path / ".cf" / "project.json").write_text(json.dumps(project, indent=2))
    return tmp_path


class _Result:
    def __init__(self, res):
        self.exit_code = res.exit_code
        self.output = " ".join(res.output.split())  # undo Rich line wrapping


def run(args, **kw):
    return _Result(CliRunner().invoke(main, args, catch_exceptions=False, **kw))


class TestCli:
    def test_generate_and_check(self, openframe_project):
        root = str(openframe_project)
        res = run(["openframe", "generate", "--check", "--project-root", root])
        assert res.exit_code == 1 and "missing or out of date" in res.output

        res = run(["openframe", "generate", "--project-root", root])
        assert res.exit_code == 0, res.output
        rtl = (openframe_project / GPIO_RTL_REL).read_text()
        assert rtl == render_outputs(validate_spec(timer_spec()))[GPIO_RTL_REL]
        power = json.loads((openframe_project / POWER_JSON_REL).read_text())
        assert power["PDN_MACRO_CONNECTIONS"] == ["mprj vccd1 vssd1 vccd1 vssd1"]

        assert run(["openframe", "generate", "--check", "--project-root", root]).exit_code == 0
        res = run(["openframe", "generate", "--project-root", root])
        assert "up to date" in res.output

        (openframe_project / POWER_JSON_REL).write_text("{}")
        assert run(["openframe", "generate", "--check", "--project-root", root]).exit_code == 1

    def test_generate_rejects_invalid_spec(self, openframe_project):
        pj = openframe_project / ".cf" / "project.json"
        data = json.loads(pj.read_text())
        data["project"]["openframe"]["gpio"]["pads"][3]["mode"] = "input"
        pj.write_text(json.dumps(data))
        res = run(["openframe", "generate", "--project-root", str(openframe_project)])
        assert res.exit_code != 0
        assert "mixes directions" in res.output
        assert not (openframe_project / GPIO_RTL_REL).exists()

    def test_generate_requires_template_layout(self, openframe_project):
        shutil.rmtree(openframe_project / "openlane")
        res = run(["openframe", "generate", "--project-root", str(openframe_project)])
        assert res.exit_code != 0 and "does not exist" in res.output

    def test_missing_block(self, openframe_project):
        pj = openframe_project / ".cf" / "project.json"
        pj.write_text(json.dumps({"project": {"type": "openframe"}}))
        res = run(["openframe", "validate", "--project-root", str(openframe_project)])
        assert res.exit_code != 0 and "no project.openframe block" in res.output

    def test_rejects_caravel_project(self, openframe_project):
        pj = openframe_project / ".cf" / "project.json"
        pj.write_text(json.dumps({"project": {"type": "digital"}}))
        res = run(["openframe", "generate", "--project-root", str(openframe_project)])
        assert res.exit_code != 0 and "for openframe projects" in res.output

    def test_export_import(self, openframe_project, tmp_path_factory):
        root = str(openframe_project)
        out = tmp_path_factory.mktemp("x") / "spec.yaml"
        assert run(["openframe", "export", str(out), "--project-root", root]).exit_code == 0
        assert validate_spec(read_spec_file(out)).raw == validate_spec(timer_spec()).raw

        # Re-importing the identical spec needs no --force.
        res = run(["openframe", "import", str(out), "--project-root", root, "--no-generate"])
        assert res.exit_code == 0, res.output

        edited = read_spec_file(out)
        edited["gpio"]["pads"][0]["mode"] = "input_pd"
        write_spec_file(out, edited)
        res = run(["openframe", "import", str(out), "--project-root", root])
        assert res.exit_code != 0 and "--force" in res.output

        res = run(["openframe", "import", str(out), "--project-root", root, "--force"])
        assert res.exit_code == 0, res.output
        stored = json.loads((openframe_project / ".cf" / "project.json").read_text())["project"]
        assert stored["openframe"]["gpio"]["pads"][0]["mode"] == "input_pd"
        assert stored["name"] == "demo"
        assert (openframe_project / GPIO_RTL_REL).exists()

    def test_import_invalid_file(self, openframe_project, tmp_path_factory):
        bad = tmp_path_factory.mktemp("x") / "bad.json"
        data = timer_spec()
        data["gpio"]["pads"].append({"pad": 2, "mode": "output", "signal": "x"})
        bad.write_text(json.dumps(data))
        res = run(["openframe", "import", str(bad), "--project-root", str(openframe_project)])
        assert res.exit_code != 0 and "pad 2 is listed more than once" in res.output

    def test_gpio_config_view_openframe(self, openframe_project):
        res = run(["gpio-config", "--view", "--project-root", str(openframe_project)])
        assert res.exit_code == 0, res.output
        assert "out[10:0]" in res.output and "13-43" in res.output

    def test_save_keeps_caravel_gpio_config(self, openframe_project):
        from chipfoundry_cli.utils import get_openframe_spec_from_project_json, save_openframe_spec_to_project_json
        pj = openframe_project / ".cf" / "project.json"
        data = json.loads(pj.read_text())
        data["project"]["gpio_config"] = {"5": "13'h1808"}
        pj.write_text(json.dumps(data))
        save_openframe_spec_to_project_json(str(pj), {"schema_version": 1})
        stored = json.loads(pj.read_text())["project"]
        assert stored["gpio_config"] == {"5": "13'h1808"}
        assert get_openframe_spec_from_project_json(str(pj)) == {"schema_version": 1}

    def test_init_seeds_from_template(self, tmp_path, monkeypatch):
        (tmp_path / ".cf").mkdir()
        (tmp_path / ".cf" / "openframe_default.json").write_text(json.dumps(timer_spec()))
        monkeypatch.setattr("chipfoundry_cli.main.load_user_config", lambda: {})
        answers = {"Project name": "demo", "Project type (digital/analog/openframe)": "openframe"}
        monkeypatch.setattr("chipfoundry_cli.main._prompt_with_default",
                            lambda label, current, detected=None: answers.get(label))
        res = run(["init", "--project-root", str(tmp_path), "--description", ""])
        assert res.exit_code == 0, res.output
        stored = json.loads((tmp_path / ".cf" / "project.json").read_text())["project"]
        assert stored["openframe"] == validate_spec(timer_spec()).raw

    def test_init_rejects_invalid_seed(self, tmp_path, monkeypatch):
        (tmp_path / ".cf").mkdir()
        bad = timer_spec()
        del bad["power"]
        (tmp_path / ".cf" / "openframe_default.json").write_text(json.dumps(bad))
        monkeypatch.setattr("chipfoundry_cli.main.load_user_config", lambda: {})
        answers = {"Project name": "demo", "Project type (digital/analog/openframe)": "openframe"}
        monkeypatch.setattr("chipfoundry_cli.main._prompt_with_default",
                            lambda label, current, detected=None: answers.get(label))
        res = run(["init", "--project-root", str(tmp_path), "--description", ""])
        assert res.exit_code != 0 and "'power' is a required property" in res.output


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

class TestTui:
    def test_edit_pad_and_save(self):
        from chipfoundry_cli.openframe.tui import OpenframeGridApp

        async def scenario():
            app = OpenframeGridApp(timer_spec())
            async with app.run_test(size=(200, 60)) as pilot:
                app.current = 13
                await pilot.press("enter")
                await pilot.pause()
                screen = app.screen
                screen.query_one("#mode_output").value = True
                signal = screen.query_one("#signal")
                signal.focus()
                await pilot.press(*"led_d")  # 'd' must go to the Input, not trigger save
                await pilot.pause()
                assert app.is_running
                screen.query_one("#ov_slow").value = True
                await pilot.click("#apply")
                await pilot.pause()
                await pilot.press("d")
                await pilot.pause()
            return app.return_value

        result = asyncio.run(scenario())
        assert result is not None
        spec = validate_spec(result)
        assert spec.pads[13].mode == "output"
        assert spec.pads[13].signal == "led_d"
        assert spec.pads[13].overrides == {"slow": 1}

    def test_save_blocked_when_invalid(self):
        from chipfoundry_cli.openframe.tui import OpenframeGridApp

        async def scenario():
            app = OpenframeGridApp(None)
            async with app.run_test(size=(200, 60)) as pilot:
                await pilot.press("d")
                await pilot.pause()
                status = str(app.query_one("#status").renderable)
                running = app.is_running
                await pilot.press("q")
            return status, running, app.return_value

        status, running, result = asyncio.run(scenario())
        assert running and "Cannot save" in status
        assert result is None

    def test_multi_select_assigns_consecutive_bits(self):
        from chipfoundry_cli.openframe.tui import OpenframeGridApp

        async def scenario():
            app = OpenframeGridApp(timer_spec())
            async with app.run_test(size=(200, 60)) as pilot:
                for pad in (20, 21, 22):
                    app.toggle_pad(pad)
                await pilot.press("enter")
                await pilot.pause()
                screen = app.screen
                screen.query_one("#mode_input_pu").value = True
                screen.query_one("#signal").value = "btn"
                await pilot.click("#apply")
                await pilot.pause()
                await pilot.press("d")
                await pilot.pause()
            return app.return_value

        spec = validate_spec(asyncio.run(scenario()))
        btn = next(s for s in spec.signals if s.name == "btn")
        assert btn.width == 3 and btn.pads == (20, 21, 22)
