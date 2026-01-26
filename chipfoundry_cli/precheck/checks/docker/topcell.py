# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Top cell check - verifies GDS has exactly one top cell."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class TopcellCheck(DockerCheck):
    """Top cell validation check."""
    
    name = "topcell"
    display_name = "Top Cell Check"
    description = "Verifies GDS has exactly one top cell"
    category = "layout"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites."""
        return self.check_gds_exists(context)
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the top cell check command."""
        gds_path = self.get_gds_path(context)
        
        cmd = f'''
python3 -c "
import pya

layout = pya.Layout()
layout.read('{gds_path}')

# Get all cells
all_cells = [c.name for c in layout.each_cell()]

# Find top cells (cells not used by any other cell)
used_cells = set()
for cell in layout.each_cell():
    for inst in cell.each_inst():
        used_cells.add(inst.cell.name)

top_cells = [c for c in all_cells if c not in used_cells]

print(f'TOP_CELLS: {{len(top_cells)}}')
print(f'TOP_CELL_NAMES: {{top_cells}}')

if len(top_cells) == 1:
    print('TOPCELL_CHECK: PASSED')
else:
    print('TOPCELL_CHECK: FAILED')
"
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse top cell check results."""
        if 'TOPCELL_CHECK: PASSED' in stdout:
            # Extract top cell name
            name_match = re.search(r"TOP_CELL_NAMES:\s*\['([^']+)'\]", stdout)
            cell_name = name_match.group(1) if name_match else 'unknown'
            return self._make_passed_result(
                f"Single top cell: {cell_name}",
                details={'top_cell': cell_name}
            )
        
        if 'TOPCELL_CHECK: FAILED' in stdout:
            # Extract count
            count_match = re.search(r'TOP_CELLS:\s*(\d+)', stdout)
            count = int(count_match.group(1)) if count_match else 0
            return self._make_failed_result(
                f"Expected 1 top cell, found {count}",
                details={'top_cell_count': count}
            )
        
        if returncode != 0:
            return self._make_failed_result(
                f"Top cell check failed (exit code {returncode})"
            )
        
        return self._make_passed_result("Top cell check completed")
