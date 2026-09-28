"""Openframe project configuration: GPIO pad spec, validation and code generation."""

from chipfoundry_cli.openframe.generate import render_outputs, stale_outputs, write_outputs  # noqa: F401
from chipfoundry_cli.openframe.spec import (  # noqa: F401
    MODES,
    NUM_PADS,
    OVERRIDES,
    POWER_DOMAINS,
    OpenframeSpec,
    SpecError,
    load_spec_from_project,
    read_spec_file,
    validate_spec,
    write_spec_file,
)
