# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Native checks that run without Docker.

These checks use only Python and standard pip packages, 
and don't require external EDA tools.
"""

# Import checks to trigger registration
from chipfoundry_cli.precheck.checks.native import gpio_defines
from chipfoundry_cli.precheck.checks.native import pdn
