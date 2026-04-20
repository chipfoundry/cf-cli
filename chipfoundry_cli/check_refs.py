"""Known cf-precheck check names with display metadata.

Must stay in sync with cf-precheck's ``ALL_CHECKS`` ordering
(see ``cf-precheck/src/cf_precheck/check_manager.py``). The backend mirrors
the ref keys in ``chipignite-backend-services/src/precheck_service/check_refs.py``.
"""

from __future__ import annotations

from typing import NamedTuple


class PrecheckCheck(NamedTuple):
    ref: str
    surname: str
    optional: bool


PRECHECK_CHECKS: tuple[PrecheckCheck, ...] = (
    PrecheckCheck("topcell_check", "Top Cell", False),
    PrecheckCheck("gpio_defines", "GPIO Defines", False),
    PrecheckCheck("pdnmulti", "PDN Multi", False),
    PrecheckCheck("metalcheck", "Metal Check", False),
    PrecheckCheck("xor", "XOR", False),
    PrecheckCheck("magic_drc", "Magic DRC", True),
    PrecheckCheck("klayout_feol", "Klayout FEOL", False),
    PrecheckCheck("klayout_beol", "Klayout BEOL", False),
    PrecheckCheck("klayout_offgrid", "Klayout Offgrid", False),
    PrecheckCheck("klayout_met_min_ca_density", "Klayout Metal Density", False),
    PrecheckCheck(
        "klayout_pin_label_purposes_overlapping_drawing",
        "Klayout Pin Label",
        False,
    ),
    PrecheckCheck("klayout_zeroarea", "Klayout ZeroArea", False),
    PrecheckCheck("spike_check", "Spike Check", False),
    PrecheckCheck("illegal_cellname_check", "Illegal Cellname", False),
    PrecheckCheck("lvs", "LVS", False),
    PrecheckCheck("oeb", "OEB", False),
)

PRECHECK_CHECK_REFS: frozenset[str] = frozenset(c.ref for c in PRECHECK_CHECKS)
