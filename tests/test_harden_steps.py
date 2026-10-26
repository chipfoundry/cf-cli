"""Tests for parsing the LibreLane step list probed by `cf harden`."""

import pytest

from chipfoundry_cli.main import _parse_librelane_steps


def test_plain_payload():
    out = 'CF_STEPS_JSON={"steps": ["Yosys.Synthesis", "OpenROAD.Floorplan"]}\n'
    assert _parse_librelane_steps(out) == ["Yosys.Synthesis", "OpenROAD.Floorplan"]


def test_terminal_escapes_around_payload():
    # Interactive terminals get a show-cursor escape from LibreLane's console.
    out = (
        '\x1b[?25lnoise\n'
        '\x1b[2KCF_STEPS_JSON={"steps": ["Yosys.Synthesis"]}\x1b[?25h\n'
        '\x1b[?25h'
    )
    assert _parse_librelane_steps(out) == ["Yosys.Synthesis"]


def test_missing_payload():
    with pytest.raises(ValueError):
        _parse_librelane_steps("\x1b[?25h")
