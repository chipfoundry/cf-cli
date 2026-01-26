# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Illegal cell name check - detects prohibited cell names in GDS."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class IllegalCellnameCheck(DockerCheck):
    """Illegal cell name detection check."""
    
    name = "illegal_cellname"
    display_name = "Illegal Cellname Check"
    description = "Detects prohibited cell names in GDS"
    category = "layout"
    supported_pdks = ['sky130A', 'sky130B']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    # Patterns for illegal cell names (PDK cells that shouldn't be directly instantiated)
    ILLEGAL_PATTERNS = [
        r'^sky130_fd_sc_.*__.*$',  # Standard cells
        r'^sky130_ef_sc_.*__.*$',  # EF standard cells
    ]
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites."""
        return self.check_gds_exists(context)
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the illegal cell name check command."""
        gds_path = self.get_gds_path(context)
        
        # Build regex patterns
        patterns_str = '|'.join(self.ILLEGAL_PATTERNS)
        
        cmd = f'''
python3 -c "
import pya
import re

layout = pya.Layout()
layout.read('{gds_path}')

# Patterns for illegal cell names
illegal_pattern = re.compile(r'{patterns_str}')

illegal_cells = []
for cell in layout.each_cell():
    if illegal_pattern.match(cell.name):
        illegal_cells.append(cell.name)

if illegal_cells:
    print(f'ILLEGAL_CELLS: {{len(illegal_cells)}}')
    # Print first few
    for cell in illegal_cells[:10]:
        print(f'  - {{cell}}')
    if len(illegal_cells) > 10:
        print(f'  ... and {{len(illegal_cells) - 10}} more')
    print('ILLEGAL_CELLNAME_CHECK: FAILED')
else:
    print('ILLEGAL_CELLNAME_CHECK: PASSED')
"
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse illegal cell name check results."""
        if 'ILLEGAL_CELLNAME_CHECK: PASSED' in stdout:
            return self._make_passed_result("No illegal cell names found")
        
        if 'ILLEGAL_CELLNAME_CHECK: FAILED' in stdout:
            # Extract count
            count_match = re.search(r'ILLEGAL_CELLS:\s*(\d+)', stdout)
            count = int(count_match.group(1)) if count_match else 0
            return self._make_failed_result(
                f"{count} illegal cell names found",
                details={'count': count}
            )
        
        if returncode != 0:
            return self._make_failed_result(
                f"Illegal cell name check failed (exit code {returncode})"
            )
        
        return self._make_passed_result("Illegal cell name check completed")
