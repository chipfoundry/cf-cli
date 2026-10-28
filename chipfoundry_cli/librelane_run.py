"""Shared LibreLane invocation helpers for `cf harden` and `cf preview`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

CONFIG_EXTS = ("json", "yaml", "yml", "tcl")
OPENLANE_DIR_NAMES = ("openlane", "librelane")
SYNTH_TO_STEP = "Yosys.Synthesis"


def find_flow_root(project_root: Path) -> Optional[Path]:
    """Return the first existing openlane/ or librelane/ directory."""
    for name in OPENLANE_DIR_NAMES:
        candidate = project_root / name
        if candidate.is_dir():
            return candidate
    return None


def find_config_file(macro_dir: Path) -> Optional[Path]:
    for ext in CONFIG_EXTS:
        path = macro_dir / f"config.{ext}"
        if path.is_file():
            return path
    return None


def list_macro_configs(project_root: Path) -> List[Dict[str, str]]:
    """Discover LibreLane macros as ``{name, config_path, flow_root}``."""
    found: List[Dict[str, str]] = []
    seen = set()
    for dir_name in OPENLANE_DIR_NAMES:
        flow_root = project_root / dir_name
        if not flow_root.is_dir():
            continue
        for child in sorted(flow_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            config = find_config_file(child)
            if config is None:
                continue
            rel = str(config.relative_to(project_root))
            if child.name in seen:
                continue
            seen.add(child.name)
            found.append(
                {
                    "name": child.name,
                    "config_path": rel,
                    "flow_root": dir_name,
                }
            )
    return found


def detect_pdk(project_root: Path, pdk_override: Optional[str] = None) -> str:
    if pdk_override:
        return pdk_override
    project_json = project_root / ".cf" / "project.json"
    if project_json.is_file():
        data = json.loads(project_json.read_text())
        pdk = data.get("pdk")
        if isinstance(pdk, str) and pdk.strip():
            return pdk.strip()
    return "sky130A"


def pdk_root_dir(project_root: Path) -> Path:
    return project_root / "dependencies" / "pdks"


def librelane_venv(flow_root: Path) -> Path:
    return flow_root / ".venv"


def resolve_execution_backend(
    *,
    openlane_version: str,
    force_nix: bool = False,
    force_docker: bool = False,
) -> Tuple[str, Optional[str]]:
    """Return ``(\"nix\"|\"docker\", error)``."""
    if force_nix and force_docker:
        return "", "Cannot use both --use-nix and --use-docker"

    use_nix = False
    if force_nix or not force_docker:
        if shutil.which("nix") is not None:
            try:
                result = subprocess.run(
                    [
                        "nix",
                        "flake",
                        "metadata",
                        f"github:chipfoundry/openlane-2/{openlane_version}",
                        "--json",
                    ],
                    capture_output=True,
                    timeout=5,
                )
                use_nix = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                use_nix = False
        if force_nix and not use_nix:
            return "", "Nix not available or cannot access LibreLane flake"

    if not use_nix:
        use_docker = False
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            use_docker = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            use_docker = False
        if force_docker and not use_docker:
            return "", "Docker not available"
        if use_docker:
            return "docker", None
        if not force_nix:
            return "", "Neither Nix nor Docker is available"

    return "nix", None


def build_librelane_command(
    *,
    project_root: Path,
    flow_root: Path,
    config_file: Path,
    pdk: str,
    pdk_root: Path,
    tag: str,
    openlane_version: str,
    backend: str,
    from_step: Optional[str] = None,
    to_step: Optional[str] = None,
    overwrite: bool = True,
) -> Tuple[List[str], Dict[str, str]]:
    """Build a Nix or Docker LibreLane command. Does not include GUI flows."""
    env = os.environ.copy()
    env.update(
        {
            "PROJECT_ROOT": str(project_root),
            "PDK_ROOT": str(pdk_root),
            "PDK": pdk,
            "LIBRELANE_RUN_TAG": tag,
        }
    )

    if backend == "nix":
        cmd = [
            "nix",
            "run",
            f"github:chipfoundry/openlane-2/{openlane_version}",
            "--",
            "--manual-pdk",
            "--pdk-root",
            str(pdk_root),
            "--pdk",
            pdk,
            "--ef-save-views-to",
            str(project_root),
            "--run-tag",
            tag,
        ]
    else:
        venv_bin = librelane_venv(flow_root) / "bin"
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        py_major = sys.version_info.major
        py_minor = sys.version_info.minor
        env["PYTHONPATH"] = str(
            librelane_venv(flow_root)
            / "lib"
            / f"python{py_major}.{py_minor}"
            / "site-packages"
        )
        cmd = [
            str(venv_bin / "python3"),
            "-m",
            "librelane",
            "-m",
            str(project_root),
            "-m",
            str(pdk_root),
        ]
        try:
            if not sys.stdin.isatty():
                cmd.append("--docker-no-tty")
        except Exception:
            cmd.append("--docker-no-tty")
        cmd.append("--dockerized")
        cmd.extend(
            [
                "--manual-pdk",
                "--pdk-root",
                str(pdk_root),
                "--pdk",
                pdk,
                "--ef-save-views-to",
                str(project_root),
                "--run-tag",
                tag,
            ]
        )

    if overwrite and not from_step:
        cmd.append("--overwrite")
    if from_step:
        cmd.extend(["--from", from_step])
    if to_step:
        cmd.extend(["--to", to_step])
    cmd.append(str(config_file))
    return cmd, env


def new_run_tag() -> str:
    return datetime.now().strftime("%y_%m_%d_%H_%M")


def double_wide_flake_dir(project_root: Path) -> Optional[Path]:
    """The double_wide project's own flake.nix dir, pinning LibreLane 3.

    double_wide ships its own flake (not cf-cli's `chipfoundry/openlane-2`
    flake used by every other project type) because the flat wrapper needs
    LibreLane 3's native PDN-to-pad routing. Returns None if the project
    doesn't have one (e.g. it predates this, or a user deleted it).
    """
    if (project_root / "flake.nix").is_file():
        return project_root
    return None


def double_wide_nix_available(flake_dir: Path, timeout: float = 20) -> bool:
    """Check that `nix develop` can resolve the project's pinned LibreLane 3 flake."""
    if shutil.which("nix") is None:
        return False
    try:
        result = subprocess.run(
            ["nix", "flake", "metadata", str(flake_dir), "--json"],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_double_wide_librelane_command(
    *,
    project_root: Path,
    flake_dir: Path,
    config_file: Path,
    pdk_root: Path,
    pdk: str,
    tag: str,
    from_step: Optional[str] = None,
    open_in_openroad: bool = False,
    open_in_klayout: bool = False,
) -> Tuple[List[str], Dict[str, str]]:
    """Build the `nix develop` command that runs LibreLane 3 on a flat double_wide wrapper.

    No `--manual-pdk`/`--pdk-root`/`--pdk` flags: LibreLane 3's default
    (Ciel-managed) PDK resolution auto-fetches the pinned PDK commit into
    `PDK_ROOT` on its own, the same project-local `dependencies/pdks` root
    every other project type's PDK already lives under (Ciel keys PDK
    versions by commit, so the two coexist without conflict).
    """
    env = os.environ.copy()
    env.update(
        {
            "PROJECT_ROOT": str(project_root),
            "PDK_ROOT": str(pdk_root),
            "PDK": pdk,
            "LIBRELANE_RUN_TAG": tag,
        }
    )

    librelane_args = []
    if open_in_openroad:
        librelane_args.extend(["--flow", "OpenInOpenROAD"])
    elif open_in_klayout:
        librelane_args.extend(["--flow", "OpenInKLayout"])
    librelane_args.extend(["--ef-save-views-to", str(project_root), "--run-tag", tag])
    if not from_step and not (open_in_openroad or open_in_klayout):
        librelane_args.append("--overwrite")
    if from_step:
        librelane_args.extend(["--from", from_step])
    librelane_args.append(str(config_file))

    cmd = ["nix", "develop", str(flake_dir), "--command", "librelane", *librelane_args]
    return cmd, env


def get_double_wide_valid_from_steps(
    *, flake_dir: Path, config_file: Path, working_dir: Path
) -> Tuple[Optional[List[str]], Optional[str]]:
    """Resolve valid `--from` step names for a double_wide config via LibreLane 3."""
    script = (
        "import json, sys\n"
        "from librelane.config import Config\n"
        "from librelane.flows import Flow, SequentialFlow\n"
        "cfg = sys.argv[1]\n"
        "target = Flow.factory.get('Classic')\n"
        "meta = Config.get_meta(cfg)\n"
        "if meta:\n"
        "    if isinstance(meta.flow, str):\n"
        "        found = Flow.factory.get(meta.flow)\n"
        "        if found is None:\n"
        "            raise RuntimeError(f\"Unknown flow '{meta.flow}' in config metadata\")\n"
        "        target = found\n"
        "    elif isinstance(meta.flow, list):\n"
        "        target = SequentialFlow.make(meta.flow)\n"
        "    if meta.substituting_steps is not None:\n"
        "        if meta.flow is None:\n"
        "            raise RuntimeError('substituting_steps is set but flow is not defined')\n"
        "        if not issubclass(target, SequentialFlow):\n"
        "            raise RuntimeError('substituting_steps requires a sequential flow')\n"
        "        target = target.Substitute(meta.substituting_steps)\n"
        "steps = []\n"
        "seen = set()\n"
        "for step in getattr(target, 'Steps', []) or []:\n"
        "    step_id = getattr(step, 'id', None)\n"
        "    if not step_id or step_id in seen:\n"
        "        continue\n"
        "    seen.add(step_id)\n"
        "    steps.append(step_id)\n"
        "print('__CF_STEPS__' + json.dumps({'steps': steps}))\n"
    )
    try:
        result = subprocess.run(
            [
                "nix",
                "develop",
                str(flake_dir),
                "--command",
                "python3",
                "-c",
                script,
                str(config_file),
            ],
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "unknown error").strip()
    marker = "__CF_STEPS__"
    for line in result.stdout.splitlines():
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker):])["steps"], None
            except Exception as exc:
                return None, str(exc)
    return None, "LibreLane did not report a step list"


def find_synth_stat_json(flow_root: Path, macro: str, run_tag: str) -> Optional[Path]:
    """Locate Yosys stat.json written by a synth-only or full LibreLane run."""
    run_dir = flow_root / macro / "runs" / run_tag
    candidates = [
        run_dir / "Yosys.Synthesis" / "reports" / "stat.json",
        run_dir / "05-yosys-synthesis" / "reports" / "stat.json",
        run_dir / "final" / "metrics.json",
    ]
    if run_dir.is_dir():
        for path in sorted(run_dir.rglob("stat.json")):
            candidates.append(path)
        for path in sorted(run_dir.rglob("metrics.json")):
            candidates.append(path)
    for path in candidates:
        if path.is_file():
            return path
    return None


def parse_synth_metrics(stat_path: Path) -> Dict[str, object]:
    """Parse Yosys stat.json or LibreLane metrics.json into gate/area fields.

    Raises ValueError if the file cannot be parsed or has no cell count.
    """
    raw = json.loads(stat_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Synth metrics at {stat_path} must be a JSON object")

    gate_count: Optional[int] = None
    area_um2: Optional[float] = None

    design = raw.get("design")
    if isinstance(design, dict):
        cells = design.get("num_cells")
        if cells is not None:
            gate_count = int(cells)
        area = design.get("area")
        if area not in (None, 0, 0.0, "0"):
            area_um2 = float(area)

    if gate_count is None and "design__instance__count" in raw:
        gate_count = int(float(raw["design__instance__count"]))
    if area_um2 is None and raw.get("design__instance__area") not in (None, "", 0, 0.0):
        area_um2 = float(raw["design__instance__area"])

    if gate_count is None:
        raise ValueError(f"No cell count in synth metrics at {stat_path}")

    area_mm2 = (area_um2 / 1_000_000.0) if area_um2 is not None else None
    return {
        "gate_count": gate_count,
        "estimated_area_mm2": area_mm2,
        "metrics_path": str(stat_path),
    }


def run_librelane(
    cmd: Sequence[str],
    *,
    cwd: Path,
    env: Dict[str, str],
) -> int:
    process = subprocess.Popen(list(cmd), cwd=str(cwd), env=env)
    return process.wait()
