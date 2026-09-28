"""Render and write every file derived from the openframe spec."""

from pathlib import Path
from typing import Dict, List

from chipfoundry_cli.openframe.gpio_gen import GPIO_RTL_REL, generate_gpio_rtl
from chipfoundry_cli.openframe.power_gen import POWER_JSON_REL, generate_power_json
from chipfoundry_cli.openframe.spec import OpenframeSpec


def render_outputs(spec: OpenframeSpec) -> Dict[str, str]:
    """Relative path -> file content for every generated file."""
    return {
        GPIO_RTL_REL: generate_gpio_rtl(spec),
        POWER_JSON_REL: generate_power_json(spec),
    }


def stale_outputs(project_root, spec: OpenframeSpec) -> List[str]:
    """Relative paths whose on-disk content differs from what the spec generates."""
    root = Path(project_root)
    stale = []
    for rel, content in render_outputs(spec).items():
        path = root / rel
        if not path.exists() or path.read_text() != content:
            stale.append(rel)
    return stale


def write_outputs(project_root, spec: OpenframeSpec) -> List[str]:
    """Write generated files and return the relative paths that changed."""
    root = Path(project_root)
    changed = []
    for rel, content in render_outputs(spec).items():
        path = root / rel
        if not path.parent.is_dir():
            raise FileNotFoundError(
                f"{path.parent} does not exist; is {root} an openframe_user_project checkout?"
            )
        if path.exists() and path.read_text() == content:
            continue
        path.write_text(content)
        changed.append(rel)
    return changed
