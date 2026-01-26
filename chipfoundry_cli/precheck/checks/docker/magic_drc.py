# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Magic DRC check."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class MagicDrcCheck(DockerCheck):
    """Magic DRC check using the Magic VLSI tool."""
    
    name = "magic_drc"
    display_name = "Magic DRC"
    description = "Design rule check using Magic VLSI tool"
    category = "drc"
    supported_pdks = ['sky130A', 'sky130B']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check that GDS file exists."""
        return self.check_gds_exists(context)
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the Magic DRC command."""
        gds_path = self.get_gds_path(context)
        report_path = context.report_dir / f'magic_drc.rpt'
        magicrc = context.pdk_path / f'libs.tech/magic/{context.pdk}.magicrc'
        
        # Magic DRC script
        magic_script = f'''
drc off
gds read {gds_path}
load {context.user_module}
select top cell
drc euclidean on
drc style drc(full)
drc check
drc catchup
set drc_count [drc count total]
puts "DRC violations: $drc_count"
drc why > {report_path}
quit
'''
        
        # Write script to temp file and run
        cmd = f'''
cat > /tmp/magic_drc.tcl << 'MAGIC_SCRIPT'
{magic_script}
MAGIC_SCRIPT
magic -noconsole -dnull -rcfile {magicrc} /tmp/magic_drc.tcl
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse Magic DRC results."""
        # Look for DRC count in output
        drc_match = re.search(r'DRC violations:\s*(\d+)', stdout)
        
        if drc_match:
            violations = int(drc_match.group(1))
            if violations > 0:
                return self._make_failed_result(
                    f"{violations} DRC violations found",
                    details={
                        'violations': violations,
                        'report': str(context.report_dir / 'magic_drc.rpt')
                    }
                )
            return self._make_passed_result("No DRC violations")
        
        # Check for errors
        if returncode != 0:
            return self._make_failed_result(
                "Magic DRC failed",
                details={'returncode': returncode, 'stderr': stderr[:500]}
            )
        
        return self._make_passed_result("DRC check completed")
