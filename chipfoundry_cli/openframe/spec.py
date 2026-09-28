"""Openframe project spec: GPIO pad modes and power domains.

The spec lives in ``.cf/project.json`` under ``project.openframe`` and is
validated in two passes: the JSON Schema shipped next to this module checks
structure, then :func:`validate_spec` checks the cross-field rules the schema
cannot express (duplicate pads, bus gaps, mixed directions, name clashes).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema
import yaml

SCHEMA_VERSION = 1
NUM_PADS = 44
SCHEMA_PATH = Path(__file__).with_name("openframe.schema.json")

MODES: Dict[str, int] = {
    "analog": 0,
    "input": 1,
    "input_pd": 2,
    "input_pu": 3,
    "output": 4,
    "bidir": 5,
}

MODE_DESCRIPTIONS: Dict[str, str] = {
    "analog": "Analog (digital buffers off)",
    "input": "Input, no pull",
    "input_pd": "Input, pull-down",
    "input_pu": "Input, pull-up",
    "output": "Output, push-pull",
    "bidir": "Bidirectional",
}

UNUSED_MODES = ("analog", "input_pd", "input_pu")

# Direction of the user-facing signal, seen from the user design.
MODE_DIRECTION: Dict[str, Optional[str]] = {
    "analog": None,
    "input": "in",
    "input_pd": "in",
    "input_pu": "in",
    "output": "out",
    "bidir": "inout",
}

# Spec override key -> CF_gpio_config parameter.
OVERRIDES: Dict[str, str] = {
    "slow": "SLOW",
    "vtrip": "VTRIP",
    "ib_mode": "IB_MODE",
    "holdover": "HOLDOVER",
    "analog_en": "ANALOG_EN",
    "analog_sel": "ANALOG_SEL",
    "analog_pol": "ANALOG_POL",
}

# Power domain (VDD net) -> ground net.
POWER_DOMAINS: Dict[str, str] = {
    "vccd1": "vssd1",
    "vccd2": "vssd2",
    "vdda1": "vssa1",
    "vdda2": "vssa2",
}

# Ports of the generated openframe_gpio module that face the padframe.
PAD_PORTS: Tuple[Tuple[str, str], ...] = (
    ("gpio_in", "input"),
    ("gpio_out", "output"),
    ("gpio_oeb", "output"),
    ("gpio_inp_dis", "output"),
    ("gpio_ib_mode_sel", "output"),
    ("gpio_vtrip_sel", "output"),
    ("gpio_slow_sel", "output"),
    ("gpio_holdover", "output"),
    ("gpio_analog_en", "output"),
    ("gpio_analog_sel", "output"),
    ("gpio_analog_pol", "output"),
    ("gpio_dm2", "output"),
    ("gpio_dm1", "output"),
    ("gpio_dm0", "output"),
    ("gpio_loopback_one", "input"),
    ("gpio_loopback_zero", "input"),
)

# Remaining openframe_project_wrapper ports; reserved so user signals never shadow them.
_WRAPPER_PORTS = (
    "gpio_in_h", "analog_io", "analog_noesd_io", "porb_h", "porb_l", "por_l",
    "resetb_h", "resetb_l", "mask_rev",
    "vdda", "vdda1", "vdda2", "vssa", "vssa1", "vssa2", "vccd", "vccd1", "vccd2",
    "vssd", "vssd1", "vssd2", "vddio", "vssio",
)

RESERVED_NAMES = frozenset([name for name, _ in PAD_PORTS] + list(_WRAPPER_PORTS))

VERILOG_KEYWORDS = frozenset("""
always and assign automatic begin buf bufif0 bufif1 case casex casez cell cmos config deassign default
defparam design disable edge else end endcase endconfig endfunction endgenerate endmodule endprimitive
endspecify endtable endtask event for force forever fork function generate genvar highz0 highz1 if ifnone
incdir include initial inout input instance integer join large liblist library localparam macromodule
medium module nand negedge nmos nor noshowcancelled not notif0 notif1 or output parameter pmos posedge
primitive pull0 pull1 pulldown pullup pulsestyle_ondetect pulsestyle_onevent rcmos real realtime reg
release repeat rnmos rpmos rtran rtranif0 rtranif1 scalared showcancelled signed small specify specparam
strong0 strong1 supply0 supply1 table task time tran tranif0 tranif1 tri tri0 tri1 triand trior trireg
unsigned use uwire vectored wait wand weak0 weak1 while wire wor xnor xor
logic bit byte int shortint longint interface endinterface class endclass package endpackage import export
""".split())

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SpecError(ValueError):
    """Raised when an openframe spec is missing or invalid. ``errors`` lists every problem found."""

    def __init__(self, errors: List[str]):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__("Invalid openframe spec:\n" + "\n".join(f"  - {e}" for e in self.errors))


@dataclass(frozen=True)
class Pad:
    pad: int
    mode: str
    signal: Optional[str] = None
    bit: Optional[int] = None
    overrides: Dict[str, int] = field(default_factory=dict)
    unused: bool = False


@dataclass(frozen=True)
class Signal:
    """A user-facing signal made of one or more pads."""

    name: str
    direction: str  # "in", "out" or "inout" (seen from the user design)
    width: Optional[int]  # None for a 1-bit signal without a bit index
    pads: Tuple[int, ...]  # pad index per bit, bit 0 first

    def ports(self) -> List[Tuple[str, str]]:
        """(port name, verilog direction) pairs on the generated module."""
        if self.direction == "in":
            return [(self.name, "output")]
        if self.direction == "out":
            return [(self.name, "input")]
        return [(f"{self.name}_in", "output"), (f"{self.name}_out", "input"), (f"{self.name}_oeb", "input")]


@dataclass(frozen=True)
class MacroPower:
    instance: str
    domain: str
    vdd_pin: str
    gnd_pin: str

    @property
    def gnd_net(self) -> str:
        return POWER_DOMAINS[self.domain]


@dataclass(frozen=True)
class OpenframeSpec:
    pads: Tuple[Pad, ...]  # all 44 pads, index == pad number
    signals: Tuple[Signal, ...]  # ordered by lowest pad index
    domains: Tuple[str, ...]
    macros: Tuple[MacroPower, ...]
    raw: Dict[str, Any]  # normalized spec dict
    unused_mode: Optional[str] = None

    @property
    def sha256(self) -> str:
        return spec_sha256(self.raw)


def load_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r") as f:
        return json.load(f)


def _schema_errors(data: Any) -> List[str]:
    validator = jsonschema.Draft7Validator(load_schema())
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path]):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{location}: {err.message}")
    return errors


def _check_name(name: str, what: str, errors: List[str]) -> None:
    if not _IDENT_RE.match(name):
        errors.append(f"{what} '{name}' is not a valid Verilog identifier")
    elif name in VERILOG_KEYWORDS:
        errors.append(f"{what} '{name}' is a Verilog keyword")
    elif name in RESERVED_NAMES:
        errors.append(f"{what} '{name}' clashes with an openframe_project_wrapper port")


def validate_spec(data: Any) -> OpenframeSpec:
    """Validate a ``project.openframe`` block and return the parsed spec.

    Raises :class:`SpecError` listing every problem found.
    """
    schema_errors = _schema_errors(data)
    if schema_errors:
        raise SpecError(schema_errors)

    errors: List[str] = []
    gpio = data["gpio"]
    unused_mode = gpio.get("unused_mode")

    by_pad: Dict[int, Pad] = {}
    for entry in gpio["pads"]:
        n = entry["pad"]
        if n in by_pad:
            errors.append(f"pad {n} is listed more than once")
            continue
        mode = entry["mode"]
        signal = entry.get("signal")
        bit = entry.get("bit")
        overrides = dict(entry.get("overrides", {}))
        if mode == "analog" and signal is not None:
            errors.append(
                f"pad {n}: analog pads cannot have a signal; use analog_io[{n}] on openframe_project_wrapper directly"
            )
        if mode in ("output", "bidir") and signal is None:
            errors.append(f"pad {n}: mode '{mode}' requires a signal to drive the pad")
        if bit is not None and signal is None:
            errors.append(f"pad {n}: 'bit' is set but 'signal' is missing")
        if signal is not None:
            _check_name(signal, f"pad {n}: signal", errors)
        by_pad[n] = Pad(pad=n, mode=mode, signal=signal, bit=bit, overrides=overrides)

    missing = [n for n in range(NUM_PADS) if n not in by_pad]
    if missing and unused_mode is None:
        errors.append(
            f"pads {_format_ranges(missing)} are not configured; list them or set gpio.unused_mode "
            f"(one of {', '.join(UNUSED_MODES)})"
        )
    for n in missing:
        by_pad[n] = Pad(pad=n, mode=unused_mode or "analog", unused=True)

    signals = _build_signals([by_pad[n] for n in sorted(by_pad) if not by_pad[n].unused], errors)

    port_owner: Dict[str, str] = {}
    for sig in signals:
        for port, _ in sig.ports():
            if port in port_owner:
                errors.append(
                    f"signal '{sig.name}' creates port '{port}', which is already used by signal '{port_owner[port]}'"
                )
            else:
                if port != sig.name:
                    _check_name(port, f"signal '{sig.name}' port", errors)
                port_owner[port] = sig.name

    power = data["power"]
    domains = tuple(power["domains"])
    macros: List[MacroPower] = []
    seen_instances = set()
    for m in power["macros"]:
        inst = m["instance"]
        if inst in seen_instances:
            errors.append(f"power: macro instance '{inst}' is listed more than once")
            continue
        seen_instances.add(inst)
        if m["domain"] not in domains:
            errors.append(
                f"power: macro '{inst}' uses domain '{m['domain']}', "
                f"which is not in power.domains ({', '.join(domains)})"
            )
        macros.append(MacroPower(instance=inst, domain=m["domain"], vdd_pin=m["vdd_pin"], gnd_pin=m["gnd_pin"]))

    if errors:
        raise SpecError(errors)

    return OpenframeSpec(
        pads=tuple(by_pad[n] for n in range(NUM_PADS)),
        signals=tuple(signals),
        domains=domains,
        macros=tuple(macros),
        raw=normalize_spec(data),
        unused_mode=unused_mode,
    )


def _build_signals(pads: List[Pad], errors: List[str]) -> List[Signal]:
    groups: Dict[str, List[Pad]] = {}
    for p in pads:
        if p.signal is not None:
            groups.setdefault(p.signal, []).append(p)

    signals = []
    for name, members in groups.items():
        directions = {MODE_DIRECTION[p.mode] for p in members}
        if len(directions) > 1:
            detail = ", ".join(f"pad {p.pad}={p.mode}" for p in members)
            errors.append(f"signal '{name}' mixes directions ({detail}); all its pads must be input, output or bidir")
            continue
        direction = directions.pop()

        with_bit = [p for p in members if p.bit is not None]
        if with_bit and len(with_bit) != len(members):
            no_bit = [p.pad for p in members if p.bit is None]
            errors.append(
                f"signal '{name}': pads {_format_ranges(no_bit)} need a 'bit' because other pads of the bus set one"
            )
            continue

        if not with_bit:
            if len(members) > 1:
                errors.append(
                    f"signal '{name}' is used by pads {_format_ranges([p.pad for p in members])} without bit indices"
                )
                continue
            signals.append(Signal(name=name, direction=direction, width=None, pads=(members[0].pad,)))
            continue

        by_bit: Dict[int, int] = {}
        bad = False
        for p in members:
            if p.bit in by_bit:
                errors.append(f"signal '{name}': bit {p.bit} is assigned to both pad {by_bit[p.bit]} and pad {p.pad}")
                bad = True
            else:
                by_bit[p.bit] = p.pad
        if bad:
            continue
        width = max(by_bit) + 1
        gaps = [b for b in range(width) if b not in by_bit]
        if gaps:
            errors.append(f"signal '{name}[{width - 1}:0]' has no pad for bit(s) {_format_ranges(gaps)}")
            continue
        signals.append(Signal(name=name, direction=direction, width=width, pads=tuple(by_bit[b] for b in range(width))))

    signals.sort(key=lambda s: min(s.pads))
    return signals


def normalize_spec(data: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical form: pads sorted by index, keys in a fixed order."""
    gpio = data["gpio"]
    pads = []
    for entry in sorted(gpio["pads"], key=lambda e: e["pad"]):
        out: Dict[str, Any] = {"pad": entry["pad"], "mode": entry["mode"]}
        if "signal" in entry:
            out["signal"] = entry["signal"]
        if "bit" in entry:
            out["bit"] = entry["bit"]
        if entry.get("overrides"):
            out["overrides"] = {k: entry["overrides"][k] for k in OVERRIDES if k in entry["overrides"]}
        pads.append(out)
    norm_gpio: Dict[str, Any] = {}
    if "unused_mode" in gpio:
        norm_gpio["unused_mode"] = gpio["unused_mode"]
    norm_gpio["pads"] = pads
    power = data["power"]
    return {
        "schema_version": data["schema_version"],
        "gpio": norm_gpio,
        "power": {
            "domains": list(power["domains"]),
            "macros": [
                {"instance": m["instance"], "domain": m["domain"], "vdd_pin": m["vdd_pin"], "gnd_pin": m["gnd_pin"]}
                for m in power["macros"]
            ],
        },
    }


def spec_sha256(data: Dict[str, Any]) -> str:
    canonical = json.dumps(normalize_spec(data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _format_ranges(nums: List[int]) -> str:
    nums = sorted(nums)
    if not nums:
        return "-"
    out = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


# ---------------------------------------------------------------------------
# File I/O (standalone YAML/JSON and .cf/project.json)
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise SpecError([f"duplicate key '{key}'"])
        out[key] = value
    return out


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping_no_dupes(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise SpecError([f"duplicate key '{key}' (line {key_node.start_mark.line + 1})"])
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_dupes)


def _file_format(path: Path, fmt: Optional[str]) -> str:
    if fmt:
        if fmt not in ("json", "yaml"):
            raise SpecError([f"unknown format '{fmt}'; use json or yaml"])
        return fmt
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    raise SpecError([f"cannot infer format from '{path.name}'; use a .json/.yaml/.yml extension or pass --format"])


def read_spec_file(path, fmt: Optional[str] = None) -> Dict[str, Any]:
    """Read a standalone spec file. Accepts either the bare block or ``{"openframe": {...}}``."""
    path = Path(path)
    fmt = _file_format(path, fmt)
    text = path.read_text()
    try:
        if fmt == "json":
            data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        else:
            data = yaml.load(text, Loader=_StrictSafeLoader)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise SpecError([f"{path}: cannot parse {fmt}: {e}"])
    if isinstance(data, dict) and set(data.keys()) == {"openframe"}:
        data = data["openframe"]
    if not isinstance(data, dict):
        raise SpecError([f"{path}: expected a mapping at the top level"])
    return data


def write_spec_file(path, data: Dict[str, Any], fmt: Optional[str] = None) -> None:
    path = Path(path)
    fmt = _file_format(path, fmt)
    data = normalize_spec(data)
    if fmt == "json":
        path.write_text(json.dumps(data, indent=2) + "\n")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=None))


def load_spec_from_project(project_root) -> OpenframeSpec:
    """Load and validate ``project.openframe`` from ``<project_root>/.cf/project.json``."""
    from chipfoundry_cli.utils import CF_PROJECT_JSON_REL, get_openframe_spec_from_project_json

    project_json = Path(project_root) / CF_PROJECT_JSON_REL
    if not project_json.exists():
        raise SpecError([f"{project_json} not found; run 'cf init' first"])
    block = get_openframe_spec_from_project_json(str(project_json))
    if block is None:
        raise SpecError([
            f"{project_json} has no project.openframe block; run 'cf gpio-config' or 'cf openframe import <file>'"
        ])
    return validate_spec(block)
