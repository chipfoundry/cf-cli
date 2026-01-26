# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Metal check - verifies metal layer usage for mini projects."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class MetalCheck(DockerCheck):
    """Metal layer check for mini projects."""
    
    name = "metal"
    display_name = "Metal Check"
    description = "Verifies no Metal 5 or Via 4 usage in mini projects"
    category = "layout"
    supported_pdks = ['sky130A', 'sky130B']
    supported_types = ['mini']  # Only for mini projects
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites."""
        return self.check_gds_exists(context)
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the metal check command."""
        gds_path = self.get_gds_path(context)
        
        # Layer numbers for Metal 5 and Via 4 in SKY130
        # met5.drawing: 72/20, via4.drawing: 71/44
        cmd = f'''
python3 -c "
import pya

layout = pya.Layout()
layout.read('{gds_path}')

# SKY130 layer definitions
MET5_LAYER = layout.layer(72, 20)
VIA4_LAYER = layout.layer(71, 44)

met5_found = False
via4_found = False

for cell in layout.each_cell():
    if cell.shapes(MET5_LAYER).size() > 0:
        met5_found = True
    if cell.shapes(VIA4_LAYER).size() > 0:
        via4_found = True

if met5_found or via4_found:
    violations = []
    if met5_found:
        violations.append('Metal 5')
    if via4_found:
        violations.append('Via 4')
    print(f'METAL_CHECK: FAILED - Found: {{\", \".join(violations)}}')
else:
    print('METAL_CHECK: PASSED')
"
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse metal check results."""
        if 'METAL_CHECK: PASSED' in stdout:
            return self._make_passed_result("No Metal 5 or Via 4 found")
        
        failed_match = re.search(r'METAL_CHECK: FAILED - Found: (.+)', stdout)
        if failed_match:
            violations = failed_match.group(1)
            return self._make_failed_result(
                f"Prohibited layers found: {violations}",
                details={'violations': violations}
            )
        
        if returncode != 0:
            return self._make_failed_result(
                f"Metal check failed (exit code {returncode})"
            )
        
        return self._make_passed_result("Metal check completed")
