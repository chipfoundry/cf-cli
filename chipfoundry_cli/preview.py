"""`cf preview`: scan macros, classify LibreLane integration, optional lint+synth."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import click
from rich.console import Console
from rich.table import Table

from chipfoundry_cli.librelane_run import (
    SYNTH_TO_STEP,
    build_librelane_command,
    detect_pdk,
    find_config_file,
    find_flow_root,
    find_synth_stat_json,
    librelane_venv,
    list_macro_configs,
    new_run_tag,
    parse_synth_metrics,
    pdk_root_dir,
    resolve_execution_backend,
    run_librelane,
)

console = Console()

STRATEGY_HIER_MACRO_FIRST = "hierarchical_macro_first"
STRATEGY_HIER_MACRO_FIRST_LEGACY = "hierarchical_macro_first_legacy"
STRATEGY_ANALOG_PG_WRAP = "analog_pg_wrap"
STRATEGY_FLATTENED_RTL = "flattened_rtl"
STRATEGY_HIER_RTL = "hierarchical_rtl"
STRATEGY_HARD_IP = "hard_ip"

LINT_PASSED = "passed"
LINT_FAILED = "failed"
LINT_SKIPPED = "skipped"

_ELABORATE_ONLY_STRATEGIES = {
    STRATEGY_HIER_MACRO_FIRST,
    STRATEGY_HIER_MACRO_FIRST_LEGACY,
    STRATEGY_ANALOG_PG_WRAP,
    STRATEGY_HARD_IP,
}

_LEGACY_BLACKBOX_SKIP = {"defines.v"}


def _truthy(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return False


def _parse_config_file(path: Path) -> Dict[str, Any]:
    raw = path.read_text()
    suffix = path.suffix.lower()
    if suffix == ".json":
        parsed = json.loads(raw)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ValueError(
                f"Cannot parse {path}: PyYAML is not installed"
            ) from exc
        parsed = yaml.safe_load(raw)
    else:
        raise ValueError(f"Unsupported config format for hierarchy scan: {path}")
    if not isinstance(parsed, dict):
        raise ValueError(f"Macro config {path} must contain an object at its root")
    return parsed


def die_area_mm2(config: Dict[str, Any]) -> Optional[Decimal]:
    """Convert LibreLane DIE_AREA (µm) to mm². Nested PDK maps are not walked."""
    raw = config.get("DIE_AREA")
    nums: List[float] = []
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            nums = [float(x) for x in raw[:4]]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid DIE_AREA list: {raw}") from exc
    elif isinstance(raw, str):
        parts = raw.replace(",", " ").split()
        if len(parts) >= 4:
            try:
                nums = [float(p) for p in parts[:4]]
            except ValueError as exc:
                raise ValueError(f"Invalid DIE_AREA string: {raw}") from exc
    if len(nums) < 4:
        return None
    width = abs(nums[2] - nums[0])
    height = abs(nums[3] - nums[1])
    um2 = width * height
    if um2 <= 0:
        return None
    return (Decimal(str(um2)) / Decimal("1000000")).quantize(Decimal("0.0001"))


def _stem_from_view_path(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    name = Path(value.replace("dir::", "").strip()).name
    if name in _LEGACY_BLACKBOX_SKIP:
        return None
    lower = name.lower()
    for ext in (".gds.gz", ".lef", ".gds", ".v"):
        if lower.endswith(ext):
            return name[: -len(ext)]
    stem = Path(name).stem
    return stem or None


def _as_path_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _macros_have_gds_lef(macros: Dict[str, Any]) -> bool:
    for child in macros.values():
        if not isinstance(child, dict):
            continue
        if _as_path_list(child.get("gds")) or _as_path_list(child.get("lef")):
            return True
    return False


def classify_integration_strategy(
    config: Dict[str, Any],
    *,
    child_names_with_configs: Set[str],
) -> str:
    macros = config.get("MACROS")
    has_macros = isinstance(macros, dict) and len(macros) > 0
    elaborate = _truthy(config.get("SYNTH_ELABORATE_ONLY"))
    has_pdn = bool(config.get("PDN_MACRO_CONNECTIONS"))
    has_abstract = bool(config.get("MAGIC_EXT_ABSTRACT_CELLS"))
    has_blackbox = bool(config.get("VERILOG_FILES_BLACKBOX"))
    has_extra_lef = bool(config.get("EXTRA_LEFS"))
    has_extra_gds = bool(config.get("EXTRA_GDS_FILES"))
    has_placement = bool(config.get("MACRO_PLACEMENT_CFG"))
    has_legacy_pdn = bool(config.get("FP_PDN_MACRO_HOOKS"))
    legacy_hier = has_blackbox or has_extra_lef or has_extra_gds or has_placement or has_legacy_pdn

    if elaborate and has_abstract and has_pdn:
        return STRATEGY_ANALOG_PG_WRAP
    if elaborate and has_macros and _macros_have_gds_lef(macros):
        return STRATEGY_HIER_MACRO_FIRST
    if elaborate and legacy_hier:
        return STRATEGY_HIER_MACRO_FIRST_LEGACY
    if has_macros:
        child_keys = {str(k).strip() for k in macros.keys() if str(k).strip()}
        if child_keys & child_names_with_configs and not _macros_have_gds_lef(macros):
            return STRATEGY_HIER_RTL
        if _macros_have_gds_lef(macros):
            return STRATEGY_HIER_MACRO_FIRST
        if child_keys & child_names_with_configs:
            return STRATEGY_HIER_RTL
    if legacy_hier and not elaborate:
        return STRATEGY_HARD_IP
    if has_macros:
        return STRATEGY_HIER_RTL
    return STRATEGY_FLATTENED_RTL


def _instance_count(child_config: Any) -> Optional[int]:
    if not isinstance(child_config, dict):
        return None
    instances = child_config.get("instances")
    if instances is None:
        return 0
    if isinstance(instances, dict):
        return len(instances)
    if isinstance(instances, list):
        return len(instances)
    raise ValueError("MACROS instances must be an object or array")


def _parse_placement_cfg(path: Path) -> int:
    count = 0
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def read_ipm_dependencies(project_root: Path) -> List[Dict[str, str]]:
    """Read ``ip/dependencies.json`` (IPM). Empty if the file is absent."""
    path = project_root / "ip" / "dependencies.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    entries = data.get("IP")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError(f"{path} field 'IP' must be an array")
    out: List[Dict[str, str]] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError(f"{path} IP[{index}] must be a single-key object")
        name, version = next(iter(item.items()))
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path} IP[{index}] has an invalid name")
        out.append({"name": name.strip(), "version": str(version), "source": "ipm"})
    return out


def _legacy_child_names(config: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    seen: Set[str] = set()
    for key in ("EXTRA_LEFS", "EXTRA_GDS_FILES", "VERILOG_FILES_BLACKBOX"):
        for raw in _as_path_list(config.get(key)):
            stem = _stem_from_view_path(raw)
            if stem and stem not in seen:
                seen.add(stem)
                names.append(stem)
    return names


def scan_project(project_root: Path) -> Dict[str, Any]:
    """Scan LibreLane configs, hierarchy, IPM, and integration strategy."""
    notes: List[str] = []
    discovered = list_macro_configs(project_root)
    configs_by_dir_name = {row["name"]: row for row in discovered}
    dir_names_with_configs = set(configs_by_dir_name)

    macros_by_name: Dict[str, Dict[str, Any]] = {}
    edges: Set[Tuple[str, str]] = set()
    parsed_by_dir: Dict[str, Dict[str, Any]] = {}

    for row in discovered:
        path = project_root / row["config_path"]
        if path.suffix.lower() == ".tcl":
            notes.append(
                f"Macro config {row['config_path']} is Tcl; hierarchy was not inferred from it"
            )
            parent = row["name"]
            macros_by_name.setdefault(
                parent,
                {
                    "name": parent,
                    "notes": f"from {row['config_path']}",
                    "is_top_level": False,
                    "source": "cli_preview",
                    "config_path": row["config_path"],
                    "estimated_area_mm2": None,
                    "gate_count": None,
                    "instances": [],
                    "integration_strategy": None,
                    "lint_status": None,
                },
            )
            continue
        try:
            config = _parse_config_file(path)
        except (ValueError, json.JSONDecodeError) as exc:
            notes.append(str(exc))
            continue
        parsed_by_dir[row["name"]] = config

        design_name = config.get("DESIGN_NAME")
        if not isinstance(design_name, str) or not design_name.strip():
            notes.append(f"Macro config {row['config_path']} has no valid DESIGN_NAME")
            parent = row["name"]
        else:
            parent = design_name.strip()

        area = die_area_mm2(config)
        parent_row = macros_by_name.setdefault(
            parent,
            {
                "name": parent,
                "notes": f"from {row['config_path']}",
                "is_top_level": False,
                "source": "cli_preview",
                "config_path": row["config_path"],
                "estimated_area_mm2": str(area) if area is not None else None,
                "gate_count": None,
                "instances": [],
                "integration_strategy": None,
                "lint_status": None,
            },
        )
        if parent_row["config_path"] != row["config_path"]:
            notes.append(
                f"Multiple configs declare DESIGN_NAME {parent!r}; using {parent_row['config_path']}"
            )
        elif area is not None and parent_row["estimated_area_mm2"] is None:
            parent_row["estimated_area_mm2"] = str(area)

        macro_section = config.get("MACROS")
        if isinstance(macro_section, dict):
            for child_name, child_config in macro_section.items():
                if not isinstance(child_name, str) or not child_name.strip():
                    notes.append(f"Macro config {row['config_path']} contains an invalid MACROS key")
                    continue
                child = child_name.strip()
                try:
                    count = _instance_count(child_config)
                except ValueError as exc:
                    notes.append(f"Macro {child!r} in {row['config_path']}: {exc}")
                    continue
                if count is None:
                    notes.append(f"Macro {child!r} in {row['config_path']} is not an object")
                    continue
                child_row = macros_by_name.setdefault(
                    child,
                    {
                        "name": child,
                        "notes": None,
                        "is_top_level": False,
                        "source": "cli_preview",
                        "config_path": None,
                        "estimated_area_mm2": None,
                        "gate_count": None,
                        "instances": [],
                        "integration_strategy": STRATEGY_HARD_IP,
                        "lint_status": None,
                    },
                )
                if count > 0:
                    child_row["instances"].append({"parent_macro": parent, "count": count})
                    edges.add((parent, child))
        elif macro_section is not None:
            notes.append(f"Macro config {row['config_path']} has MACROS that is not an object")

        placement_count = None
        placement = config.get("MACRO_PLACEMENT_CFG")
        if isinstance(placement, str) and placement.strip():
            rel = placement.replace("dir::", "")
            placement_path = (path.parent / rel).resolve()
            if placement_path.is_file():
                placement_count = _parse_placement_cfg(placement_path)
            else:
                notes.append(f"MACRO_PLACEMENT_CFG not found for {row['config_path']}: {placement}")

        for child in _legacy_child_names(config):
            child_row = macros_by_name.setdefault(
                child,
                {
                    "name": child,
                    "notes": None,
                    "is_top_level": False,
                    "source": "cli_preview",
                    "config_path": None,
                    "estimated_area_mm2": None,
                    "gate_count": None,
                    "instances": [],
                    "integration_strategy": STRATEGY_HARD_IP,
                    "lint_status": None,
                },
            )
            count = placement_count if placement_count and placement_count > 0 else 1
            if not any(inst["parent_macro"] == parent for inst in child_row["instances"]):
                child_row["instances"].append({"parent_macro": parent, "count": count})
                edges.add((parent, child))

    child_config_names: Set[str] = set()
    for name in macros_by_name:
        if name in dir_names_with_configs:
            child_config_names.add(name)
        # DESIGN_NAME may differ from directory name
        for dir_name, config in parsed_by_dir.items():
            design = config.get("DESIGN_NAME")
            if isinstance(design, str) and design.strip() == name:
                child_config_names.add(name)

    for dir_name, config in parsed_by_dir.items():
        design = config.get("DESIGN_NAME")
        parent = design.strip() if isinstance(design, str) and design.strip() else dir_name
        strategy = classify_integration_strategy(
            config, child_names_with_configs=child_config_names
        )
        if parent in macros_by_name:
            macros_by_name[parent]["integration_strategy"] = strategy

    if macros_by_name:
        children = {child for _, child in edges}
        roots = sorted(set(macros_by_name) - children)
        if len(roots) == 1:
            macros_by_name[roots[0]]["is_top_level"] = True
            notes.append(f"Identified unique design hierarchy root: {roots[0]}")
        elif len(roots) > 1:
            notes.append(
                "Multiple design hierarchy roots found; no macro was marked top-level: "
                + ", ".join(roots)
            )
        else:
            notes.append("No design hierarchy root found; the macro graph may contain a cycle")

    ipm = read_ipm_dependencies(project_root)
    marketplace_ip: List[Dict[str, str]] = list(ipm)
    seen_ip = {item["name"] for item in marketplace_ip}
    for name, row in macros_by_name.items():
        if row.get("integration_strategy") == STRATEGY_HARD_IP and name not in seen_ip:
            marketplace_ip.append({"name": name, "version": "", "source": "config"})
            seen_ip.add(name)
        elif name.startswith("CF_") and name not in seen_ip:
            marketplace_ip.append({"name": name, "version": "", "source": "config"})
            seen_ip.add(name)

    if discovered:
        notes.append(f"Found {len(discovered)} OpenLane/LibreLane macro config(s)")
    if ipm:
        notes.append(f"Found {len(ipm)} IPM dependenc(ies) in ip/dependencies.json")

    macros = [macros_by_name[name] for name in sorted(macros_by_name)]
    return {
        "macros": macros,
        "marketplace_ip": marketplace_ip,
        "notes": notes,
        "discovered": discovered,
        "parsed_by_dir": {k: True for k in parsed_by_dir},
    }


def strategy_uses_gate_count(strategy: Optional[str]) -> bool:
    return strategy == STRATEGY_FLATTENED_RTL or strategy == STRATEGY_HIER_RTL


def find_verilator_lint_errors(flow_root: Path, macro: str, run_tag: str) -> Optional[int]:
    run_dir = flow_root / macro / "runs" / run_tag
    if not run_dir.is_dir():
        return None
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if "lint" in name and path.suffix in {".rpt", ".log", ".txt"}:
            text = path.read_text(errors="replace")
            errors = len(re.findall(r"\berror\b", text, flags=re.IGNORECASE))
            return errors
    metrics = run_dir / "final" / "metrics.json"
    if metrics.is_file():
        data = json.loads(metrics.read_text())
        if isinstance(data, dict) and "verilator__lint_error__count" in data:
            return int(float(data["verilator__lint_error__count"]))
    return 0


def apply_synth_metrics(
    macro: Dict[str, Any],
    metrics: Dict[str, Any],
    *,
    lint_errors: Optional[int],
) -> None:
    strategy = macro.get("integration_strategy")
    if lint_errors is None:
        macro["lint_status"] = LINT_SKIPPED
    elif lint_errors == 0:
        macro["lint_status"] = LINT_PASSED
    else:
        macro["lint_status"] = LINT_FAILED
    if strategy_uses_gate_count(strategy):
        macro["gate_count"] = metrics["gate_count"]
        area = metrics.get("estimated_area_mm2")
        if area is not None:
            macro["estimated_area_mm2"] = str(area)
    elif macro.get("estimated_area_mm2") is None and metrics.get("estimated_area_mm2") is not None:
        # Keep DIE_AREA / catalog area; do not treat Yosys cells as gates.
        pass


def leaf_summary(macros: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    parent_names = {
        inst["parent_macro"]
        for row in macros
        for inst in row.get("instances") or []
    }
    known_area = Decimal("0")
    known_gates = 0
    missing_area = 0
    missing_gates = 0
    instantiated = 0
    leaf_count = 0
    for row in macros:
        is_leaf = (not row.get("is_top_level")) and row["name"] not in parent_names
        if not is_leaf:
            continue
        leaf_count += 1
        instance_count = sum(int(inst["count"]) for inst in (row.get("instances") or []))
        if instance_count == 0:
            continue
        instantiated += 1
        area = row.get("estimated_area_mm2")
        if area in (None, ""):
            missing_area += 1
        else:
            known_area += Decimal(str(area)) * instance_count
        gates = row.get("gate_count")
        if gates is None:
            missing_gates += 1
        else:
            known_gates += int(gates) * instance_count
    total_area = known_area if instantiated > 0 and missing_area == 0 else None
    total_gates = known_gates if instantiated > 0 and missing_gates == 0 else None
    return {
        "leaf_macro_count": leaf_count,
        "instantiated_leaf_macro_count": instantiated,
        "known_area_mm2": str(known_area),
        "total_area_mm2": str(total_area) if total_area is not None else None,
        "missing_area_macro_count": missing_area,
        "known_gate_count": known_gates,
        "total_gate_count": total_gates,
        "missing_gate_count_macro_count": missing_gates,
    }


def print_preview_report(scan: Dict[str, Any], summary: Dict[str, Any]) -> None:
    table = Table(title="Project preview", show_lines=False)
    table.add_column("Macro", style="cyan")
    table.add_column("Strategy")
    table.add_column("Hierarchy")
    table.add_column("Gates", justify="right")
    table.add_column("Area mm²", justify="right")
    table.add_column("Lint")
    for row in scan["macros"]:
        if row.get("is_top_level"):
            hier = "Top level"
        elif row.get("instances"):
            hier = ", ".join(
                f"{inst['count']}× by {inst['parent_macro']}"
                for inst in row["instances"]
            )
        else:
            hier = "Not instantiated"
        gates = "" if row.get("gate_count") is None else f"{row['gate_count']:,}"
        area = row.get("estimated_area_mm2") or "—"
        table.add_row(
            row["name"],
            row.get("integration_strategy") or "—",
            hier,
            gates or "—",
            str(area),
            row.get("lint_status") or "—",
        )
    console.print(table)

    ip_table = Table(title="Marketplace / IPM IP", show_lines=False)
    ip_table.add_column("Name", style="cyan")
    ip_table.add_column("Version")
    ip_table.add_column("Detected from")
    if scan["marketplace_ip"]:
        for item in scan["marketplace_ip"]:
            ip_table.add_row(item["name"], item.get("version") or "—", item.get("source") or "—")
        console.print(ip_table)
    else:
        console.print("[yellow]No marketplace IP or IPM dependencies detected[/yellow]")

    console.print(
        f"[bold]Leaf macros:[/bold] {summary['leaf_macro_count']}  "
        f"instantiated {summary['instantiated_leaf_macro_count']}  "
        f"known gates {summary['known_gate_count']:,}  "
        f"known area {summary['known_area_mm2']} mm²"
    )
    for note in scan.get("notes") or []:
        console.print(f"[dim]• {note}[/dim]")


def run_local_synth_for_macros(
    project_root: Path,
    scan: Dict[str, Any],
    *,
    only_macro: Optional[str],
    pdk: Optional[str],
    use_nix: bool,
    use_docker: bool,
    dry_run: bool,
) -> None:
    from chipfoundry_cli.utils import fetch_versions_from_upstream

    flow_root = find_flow_root(project_root)
    if flow_root is None:
        raise click.ClickException("No openlane/ or librelane/ directory found")
    venv = librelane_venv(flow_root)
    if not venv.exists():
        raise click.ClickException(
            "LibreLane not installed. Run 'cf setup --only-openlane' or pass --skip-synth."
        )
    pdk_name = detect_pdk(project_root, pdk)
    pdk_root = pdk_root_dir(project_root)
    if not (pdk_root / pdk_name).exists():
        raise click.ClickException(
            f"PDK not found: {pdk_root / pdk_name}. Run 'cf setup --only-pdk' or pass --skip-synth."
        )
    versions = fetch_versions_from_upstream("chipfoundry", "cf-cli", "main")
    openlane_version = versions["openlane_version"]
    backend, err = resolve_execution_backend(
        openlane_version=openlane_version,
        force_nix=use_nix,
        force_docker=use_docker,
    )
    if err:
        raise click.ClickException(err)

    targets: List[Dict[str, Any]] = []
    for macro in scan["macros"]:
        if only_macro and macro["name"] != only_macro:
            continue
        if not macro.get("config_path"):
            continue
        if not strategy_uses_gate_count(macro.get("integration_strategy")):
            macro["lint_status"] = macro.get("lint_status") or LINT_SKIPPED
            continue
        targets.append(macro)

    if only_macro and not any(m["name"] == only_macro for m in scan["macros"]):
        raise click.ClickException(f"Macro not found: {only_macro}")

    for macro in targets:
        config_rel = macro["config_path"]
        config_file = project_root / config_rel
        dir_name = Path(config_rel).parent.name
        tag = new_run_tag()
        console.print(f"[cyan]Lint + synthesis:[/cyan] {macro['name']} → {SYNTH_TO_STEP} (tag {tag})")
        cmd, env = build_librelane_command(
            project_root=project_root,
            flow_root=flow_root,
            config_file=config_file,
            pdk=pdk_name,
            pdk_root=pdk_root,
            tag=tag,
            openlane_version=openlane_version,
            backend=backend,
            to_step=SYNTH_TO_STEP,
            overwrite=True,
        )
        if dry_run:
            console.print("[dim]" + " ".join(cmd) + "[/dim]")
            continue
        code = run_librelane(cmd, cwd=flow_root, env=env)
        if code != 0:
            raise click.ClickException(f"LibreLane preview failed for {macro['name']} (exit {code})")
        stat_path = find_synth_stat_json(flow_root, dir_name, tag)
        if stat_path is None:
            raise click.ClickException(
                f"Synthesizable macro {macro['name']} finished with no stat.json"
            )
        metrics = parse_synth_metrics(stat_path)
        lint_errors = find_verilator_lint_errors(flow_root, dir_name, tag)
        apply_synth_metrics(macro, metrics, lint_errors=lint_errors)


def build_preview_payload(
    scan: Dict[str, Any],
    *,
    synth_mode: str,
    git_sha: Optional[str],
    cli_version: str,
) -> Dict[str, Any]:
    macros = []
    for row in scan["macros"]:
        item = {
            "name": row["name"],
            "notes": row.get("notes"),
            "is_top_level": bool(row.get("is_top_level")),
            "source": "cli_preview",
            "config_path": row.get("config_path"),
            "estimated_area_mm2": row.get("estimated_area_mm2"),
            "gate_count": row.get("gate_count"),
            "instances": row.get("instances") or [],
            "integration_strategy": row.get("integration_strategy"),
            "lint_status": row.get("lint_status"),
        }
        macros.append(item)
    return {
        "macros": macros,
        "marketplace_ip": scan.get("marketplace_ip") or [],
        "notes": scan.get("notes") or [],
        "git_sha": git_sha,
        "cli_version": cli_version,
        "synth_mode": synth_mode,
        "summary": leaf_summary(scan["macros"]),
    }


def merge_remote_job_metrics(scan: Dict[str, Any], job: Dict[str, Any]) -> None:
    """Apply pnr_results stats from a completed preview job onto the matching macro."""
    macro_name = job.get("macro")
    results = job.get("pnr_results") or {}
    stats = results.get("stats") if isinstance(results, dict) else None
    if not macro_name or not isinstance(stats, dict):
        return
    gate_count = stats.get("instance_count") or stats.get("design__instance__count")
    area = stats.get("instance_area") or stats.get("design__instance__area")
    metrics: Dict[str, Any] = {}
    if gate_count is not None:
        metrics["gate_count"] = int(float(gate_count))
    if area not in (None, "", 0, 0.0):
        # LibreLane instance area is µm².
        metrics["estimated_area_mm2"] = float(area) / 1_000_000.0
    if "gate_count" not in metrics:
        return
    for macro in scan["macros"]:
        if macro["name"] != macro_name:
            continue
        apply_synth_metrics(macro, metrics, lint_errors=0)
        return


def register_preview(main_group) -> None:
    @main_group.command("preview")
    @click.option(
        "--project-root",
        type=click.Path(exists=True, file_okay=False),
        help="Path to the project directory (defaults to current directory)",
    )
    @click.option("--macro", help="Only run lint+synth for this macro (scan still covers the project)")
    @click.option("--skip-synth", is_flag=True, help="Scan and classify only; do not run LibreLane")
    @click.option("--no-push", is_flag=True, help="Print the report without posting to the platform")
    @click.option("--pdk", help="PDK to use (defaults to project.json or sky130A)")
    @click.option("--use-nix", is_flag=True, help="Force Nix for local lint+synth")
    @click.option("--use-docker", is_flag=True, help="Force Docker for local lint+synth")
    @click.option("--dry-run", is_flag=True, help="Show LibreLane commands without running them")
    @click.option("--remote", is_flag=True, help="Queue synth-only PNR jobs on the platform")
    @click.option("--poll", is_flag=True, help="With --remote: wait until jobs finish")
    @click.option("--git-ref", default="main", show_default=True, help="Git ref for remote preview jobs")
    @click.option(
        "--wait-timeout",
        type=int,
        default=7200,
        show_default=True,
        help="With --remote --poll: max seconds to wait (0 = no limit)",
    )
    def preview(
        project_root,
        macro,
        skip_synth,
        no_push,
        pdk,
        use_nix,
        use_docker,
        dry_run,
        remote,
        poll,
        git_ref,
        wait_timeout,
    ):
        """Scan macros, IP, and LibreLane strategy; optionally lint+synthesize.

        Examples:
            cf preview
            cf preview --skip-synth
            cf preview --macro user_proj_example
            cf preview --remote --poll
            cf preview --no-push
        """
        from chipfoundry_cli.main import (
            _api_post,
            _load_project_platform_id,
            _queue_and_maybe_poll_remote_job,
            check_project_initialized,
            get_project_json_from_cwd,
        )
        from chipfoundry_cli.utils import get_head_commit_sha
        import importlib.metadata

        cwd_root, _ = get_project_json_from_cwd()
        if not project_root and cwd_root:
            project_root = cwd_root
        if not project_root:
            project_root = os_getcwd()
        project_root_path = Path(project_root)

        if poll and not remote:
            console.print("[red]✗[/red] --poll requires --remote.")
            raise SystemExit(1)
        if remote and skip_synth:
            console.print("[red]✗[/red] --remote cannot be combined with --skip-synth.")
            raise SystemExit(1)
        if remote and (use_nix or use_docker):
            console.print("[red]✗[/red] --remote cannot be combined with --use-nix or --use-docker.")
            raise SystemExit(1)

        if not check_project_initialized(
            project_root_path, "preview", dry_run=dry_run, allow_graceful=True
        ):
            console.print("[red]✗[/red] Project not initialized. Please run 'cf init' first.")
            return

        scan = scan_project(project_root_path)
        if not scan["macros"] and not scan["marketplace_ip"]:
            console.print("[yellow]No LibreLane macros or IPM dependencies found[/yellow]")

        synth_mode = "skipped"
        if skip_synth:
            for row in scan["macros"]:
                row["lint_status"] = LINT_SKIPPED
        elif remote:
            synth_mode = "remote"
        else:
            synth_mode = "local"
            if not dry_run:
                run_local_synth_for_macros(
                    project_root_path,
                    scan,
                    only_macro=macro,
                    pdk=pdk,
                    use_nix=use_nix,
                    use_docker=use_docker,
                    dry_run=False,
                )
            else:
                run_local_synth_for_macros(
                    project_root_path,
                    scan,
                    only_macro=macro,
                    pdk=pdk,
                    use_nix=use_nix,
                    use_docker=use_docker,
                    dry_run=True,
                )

        summary = leaf_summary(scan["macros"])
        print_preview_report(scan, summary)

        git_sha = None
        try:
            git_sha = get_head_commit_sha(str(project_root_path))
        except Exception:
            git_sha = None
        cli_version = importlib.metadata.version("chipfoundry-cli")
        payload = build_preview_payload(
            scan,
            synth_mode=synth_mode,
            git_sha=git_sha,
            cli_version=cli_version,
        )

        platform_id = _load_project_platform_id(str(project_root_path))
        if remote:
            if not platform_id:
                console.print(
                    "[red]✗[/red] Link this repo to a platform project (set platform_project_id via [bold]cf link[/bold])."
                )
                raise SystemExit(1)
            if not no_push:
                _api_post(f"/projects/{platform_id}/preview", payload)
                console.print("[green]✓ Preview scan synced to platform[/green]")
            targets = [
                row
                for row in scan["macros"]
                if row.get("config_path") and strategy_uses_gate_count(row.get("integration_strategy"))
            ]
            if macro:
                targets = [row for row in targets if row["name"] == macro]
            from chipfoundry_cli.remote_precheck_git import (
                RemotePrecheckGitError,
                verify_remote_job_repo,
            )

            try:
                verify_remote_job_repo(project_root_path, git_ref)
            except RemotePrecheckGitError as e:
                console.print(f"[red]✗[/red] {e}")
                raise SystemExit(1)
            for row in targets:
                params = [
                    ("macro", row["name"]),
                    ("git_ref", git_ref),
                    ("purpose", "preview"),
                    ("to_step", SYNTH_TO_STEP),
                ]
                if pdk:
                    params.append(("pdk", pdk))
                job = _queue_and_maybe_poll_remote_job(
                    create_path=f"/projects/{platform_id}/pnr-jobs",
                    job_get_path_template=f"/projects/{platform_id}/pnr-jobs/{{jid}}",
                    params=params,
                    dry_run=dry_run,
                    poll=poll,
                    wait_timeout=wait_timeout,
                    label=f"Remote preview ({row['name']})",
                )
                if poll and isinstance(job, dict):
                    merge_remote_job_metrics(scan, job)
            if poll and not no_push and not dry_run:
                payload = build_preview_payload(
                    scan,
                    synth_mode="remote",
                    git_sha=git_sha,
                    cli_version=cli_version,
                )
                _api_post(f"/projects/{platform_id}/preview", payload)
                console.print("[green]✓ Preview synthesis metrics synced to platform[/green]")
            return

        if no_push or dry_run:
            return
        if not platform_id:
            console.print(
                "[yellow]⚠ Preview not synced: link this repo with [bold]cf link[/bold][/yellow]"
            )
            return
        _api_post(f"/projects/{platform_id}/preview", payload)
        console.print("[green]✓ Preview results synced to platform[/green]")


def os_getcwd() -> str:
    import os

    return os.getcwd()
