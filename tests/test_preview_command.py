"""Tests for cf preview scan, strategy classification, and command wiring."""

import json
from pathlib import Path

from click.testing import CliRunner

from chipfoundry_cli.librelane_run import parse_synth_metrics
from chipfoundry_cli.main import main
from chipfoundry_cli.preview import (
    STRATEGY_ANALOG_PG_WRAP,
    STRATEGY_FLATTENED_RTL,
    STRATEGY_HARD_IP,
    STRATEGY_HIER_MACRO_FIRST,
    STRATEGY_HIER_MACRO_FIRST_LEGACY,
    apply_synth_metrics,
    classify_integration_strategy,
    die_area_mm2,
    leaf_summary,
    scan_project,
)


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)


def _init_project(root: Path) -> None:
    _write(
        root / ".cf" / "project.json",
        json.dumps({"project": {"name": "demo", "platform_project_id": None}}),
    )


def test_preview_help():
    runner = CliRunner()
    result = runner.invoke(main, ["preview", "--help"])
    assert result.exit_code == 0
    assert "Scan macros" in result.output
    assert "--skip-synth" in result.output
    assert "--no-push" in result.output
    assert "--remote" in result.output


def test_die_area_list_and_string():
    assert str(die_area_mm2({"DIE_AREA": [0, 0, 1000, 2000]})) == "2.0000"
    assert str(die_area_mm2({"DIE_AREA": "0 0 2920 3520"})) == "10.2784"
    assert die_area_mm2({}) is None


def test_classify_caravel_wrapper_and_leaf():
    wrapper = {
        "SYNTH_ELABORATE_ONLY": True,
        "MACROS": {
            "user_proj_example": {
                "gds": ["dir::../../gds/user_proj_example.gds"],
                "lef": ["dir::../../lef/user_proj_example.lef"],
                "instances": {"mprj": {}},
            }
        },
    }
    leaf = {"DESIGN_NAME": "user_proj_example", "VERILOG_FILES": ["a.v"]}
    analog = {
        "SYNTH_ELABORATE_ONLY": True,
        "PDN_MACRO_CONNECTIONS": ["u_bgr vccd1 vssd1 vpwr vgnd"],
        "MAGIC_EXT_ABSTRACT_CELLS": ["^CF_BGR_core$"],
        "MACROS": {
            "CF_BGR": {
                "gds": ["dir::gds/CF_BGR.gds"],
                "lef": ["dir::lef/CF_BGR.lef"],
            }
        },
    }
    legacy = {
        "SYNTH_ELABORATE_ONLY": 1,
        "VERILOG_FILES_BLACKBOX": ["dir::../../verilog/rtl/user_proj_example.v"],
        "EXTRA_LEFS": "dir::../../lef/user_proj_example.lef",
        "EXTRA_GDS_FILES": "dir::../../gds/user_proj_example.gds",
        "MACRO_PLACEMENT_CFG": "dir::macro.cfg",
        "FP_PDN_MACRO_HOOKS": "mprj vccd1 vssd1 vccd1 vssd1",
    }
    kids = {"user_proj_example", "CF_BGR"}
    assert classify_integration_strategy(wrapper, child_names_with_configs=kids) == STRATEGY_HIER_MACRO_FIRST
    assert classify_integration_strategy(leaf, child_names_with_configs=kids) == STRATEGY_FLATTENED_RTL
    assert classify_integration_strategy(analog, child_names_with_configs=kids) == STRATEGY_ANALOG_PG_WRAP
    assert (
        classify_integration_strategy(legacy, child_names_with_configs=kids)
        == STRATEGY_HIER_MACRO_FIRST_LEGACY
    )


def test_scan_hierarchy_and_ipm(tmp_path: Path):
    _init_project(tmp_path)
    _write(
        tmp_path / "openlane" / "user_project_wrapper" / "config.json",
        json.dumps(
            {
                "DESIGN_NAME": "user_project_wrapper",
                "SYNTH_ELABORATE_ONLY": True,
                "DIE_AREA": [0, 0, 2920, 3520],
                "MACROS": {
                    "user_proj_example": {
                        "gds": ["dir::../../gds/user_proj_example.gds"],
                        "lef": ["dir::../../lef/user_proj_example.lef"],
                        "instances": {"mprj": {"location": [60, 15]}},
                    }
                },
            }
        ),
    )
    _write(
        tmp_path / "openlane" / "user_proj_example" / "config.json",
        json.dumps(
            {
                "DESIGN_NAME": "user_proj_example",
                "VERILOG_FILES": ["dir::../../verilog/rtl/user_proj_example.v"],
                "DIE_AREA": [0, 0, 2800, 1760],
            }
        ),
    )
    _write(
        tmp_path / "ip" / "dependencies.json",
        json.dumps({"IP": [{"CF_SRAM_1024x32": "1.2.3"}]}),
    )
    scan = scan_project(tmp_path)
    by_name = {row["name"]: row for row in scan["macros"]}
    assert by_name["user_project_wrapper"]["is_top_level"] is True
    assert by_name["user_project_wrapper"]["integration_strategy"] == STRATEGY_HIER_MACRO_FIRST
    assert by_name["user_proj_example"]["integration_strategy"] == STRATEGY_FLATTENED_RTL
    assert by_name["user_proj_example"]["instances"] == [
        {"parent_macro": "user_project_wrapper", "count": 1}
    ]
    assert any(item["name"] == "CF_SRAM_1024x32" for item in scan["marketplace_ip"])


def test_scan_legacy_extra_lefs(tmp_path: Path):
    _init_project(tmp_path)
    wrapper_dir = tmp_path / "openlane" / "openframe_project_wrapper"
    _write(
        wrapper_dir / "config.json",
        json.dumps(
            {
                "DESIGN_NAME": "openframe_project_wrapper",
                "SYNTH_ELABORATE_ONLY": 1,
                "VERILOG_FILES_BLACKBOX": [
                    "dir::../../verilog/rtl/defines.v",
                    "dir::../../verilog/rtl/user_proj_example.v",
                ],
                "EXTRA_LEFS": "dir::../../lef/user_proj_example.lef",
                "MACRO_PLACEMENT_CFG": "dir::macro.cfg",
            }
        ),
    )
    _write(wrapper_dir / "macro.cfg", "mprj 1175 1690 N\n")
    scan = scan_project(tmp_path)
    by_name = {row["name"]: row for row in scan["macros"]}
    assert by_name["openframe_project_wrapper"]["integration_strategy"] == STRATEGY_HIER_MACRO_FIRST_LEGACY
    assert by_name["user_proj_example"]["instances"][0]["count"] == 1
    assert by_name["user_proj_example"]["integration_strategy"] == STRATEGY_HARD_IP


def test_tcl_config_is_listed_without_hierarchy(tmp_path: Path):
    _init_project(tmp_path)
    _write(tmp_path / "openlane" / "legacy" / "config.tcl", "set ::env(DESIGN_NAME) legacy\n")
    scan = scan_project(tmp_path)
    assert scan["macros"][0]["name"] == "legacy"
    assert scan["macros"][0]["integration_strategy"] is None
    assert any("Tcl" in note for note in scan["notes"])


def test_parse_synth_metrics_and_skip_gates_for_wrapper(tmp_path: Path):
    stat = tmp_path / "stat.json"
    stat.write_text(json.dumps({"design": {"num_cells": 1234, "area": 50000}}))
    metrics = parse_synth_metrics(stat)
    assert metrics["gate_count"] == 1234
    assert abs(metrics["estimated_area_mm2"] - 0.05) < 1e-9

    wrapper = {
        "name": "user_project_wrapper",
        "integration_strategy": STRATEGY_HIER_MACRO_FIRST,
        "gate_count": None,
        "estimated_area_mm2": "10.2784",
    }
    apply_synth_metrics(wrapper, metrics, lint_errors=0)
    assert wrapper["gate_count"] is None
    assert wrapper["lint_status"] == "passed"
    assert wrapper["estimated_area_mm2"] == "10.2784"

    leaf = {
        "name": "user_proj_example",
        "integration_strategy": STRATEGY_FLATTENED_RTL,
        "gate_count": None,
        "estimated_area_mm2": None,
    }
    apply_synth_metrics(leaf, metrics, lint_errors=0)
    assert leaf["gate_count"] == 1234


def test_leaf_summary_partial_metrics():
    macros = [
        {
            "name": "top",
            "is_top_level": True,
            "instances": [],
            "gate_count": None,
            "estimated_area_mm2": None,
        },
        {
            "name": "leaf_a",
            "is_top_level": False,
            "instances": [{"parent_macro": "top", "count": 2}],
            "gate_count": 100,
            "estimated_area_mm2": "0.1",
        },
        {
            "name": "leaf_b",
            "is_top_level": False,
            "instances": [{"parent_macro": "top", "count": 1}],
            "gate_count": None,
            "estimated_area_mm2": None,
        },
    ]
    summary = leaf_summary(macros)
    assert summary["known_gate_count"] == 200
    assert summary["total_gate_count"] is None
    assert summary["missing_gate_count_macro_count"] == 1


def test_preview_skip_synth_no_push(tmp_path: Path):
    _init_project(tmp_path)
    _write(
        tmp_path / "openlane" / "user_proj_example" / "config.json",
        json.dumps({"DESIGN_NAME": "user_proj_example", "VERILOG_FILES": ["a.v"]}),
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["preview", "--project-root", str(tmp_path), "--skip-synth", "--no-push"],
    )
    assert result.exit_code == 0, result.output
    assert "user_proj_example" in result.output
    assert "flattened_rtl" in result.output
