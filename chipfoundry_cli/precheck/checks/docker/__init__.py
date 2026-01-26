# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Docker checks that require EDA tools.

These checks require external tools like KLayout and Magic,
which are typically only available inside a Docker container.
"""

# Import checks to trigger registration
from chipfoundry_cli.precheck.checks.docker import klayout_drc
from chipfoundry_cli.precheck.checks.docker import magic_drc
from chipfoundry_cli.precheck.checks.docker import lvs
from chipfoundry_cli.precheck.checks.docker import xor
from chipfoundry_cli.precheck.checks.docker import oeb
from chipfoundry_cli.precheck.checks.docker import consistency
from chipfoundry_cli.precheck.checks.docker import spike
from chipfoundry_cli.precheck.checks.docker import topcell
from chipfoundry_cli.precheck.checks.docker import metal
from chipfoundry_cli.precheck.checks.docker import illegal_cellname
