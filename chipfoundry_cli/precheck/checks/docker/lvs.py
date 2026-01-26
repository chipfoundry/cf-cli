# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""LVS (Layout vs Schematic) check using Magic and Netgen."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class LvsCheck(DockerCheck):
    """Layout vs Schematic verification check using Magic extraction and Netgen comparison."""
    
    name = "lvs"
    display_name = "LVS"
    description = "Layout vs Schematic verification"
    category = "lvs"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites for LVS."""
        # Check GDS exists
        gds_result = self.check_gds_exists(context)
        if gds_result:
            return gds_result
        
        # Check for verilog GL netlist
        gl_netlist = context.project_path / 'verilog' / 'gl' / f'{context.user_module}.v'
        if not gl_netlist.exists():
            return self._make_failed_result(
                f"GL netlist not found: {gl_netlist.name}",
                details={'path': str(gl_netlist)}
            )
        
        return None
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the LVS command using Magic and Netgen."""
        design_name = context.user_module
        gds_path = self.get_gds_path(context)
        output_dir = context.output_dir
        pdk_root = context.pdk_path.parent
        pdk = context.pdk
        
        gl_netlist = context.project_path / 'verilog' / 'gl' / f'{design_name}.v'
        spice_output = output_dir / f'{design_name}.spice'
        lvs_report = output_dir / f'{design_name}_lvs.rpt'
        
        # Get the magicrc path
        magicrc = f'{pdk_root}/{pdk}/libs.tech/magic/{pdk}.magicrc'
        netgen_setup = f'{pdk_root}/{pdk}/libs.tech/netgen/{pdk}_setup.tcl'
        
        # Magic extraction followed by Netgen LVS
        cmd = f'''
export PDK_ROOT={pdk_root}
export PDK={pdk}

cd {output_dir}

# Extract SPICE netlist using Magic
magic -dnull -noconsole -rcfile {magicrc} << 'MAGIC_EOF'
drc off
crashbackups stop
gds read {gds_path}
load {design_name}
select top cell
extract no all
extract do local
extract all
ext2spice lvs
ext2spice
MAGIC_EOF

rm -f *.ext 2>/dev/null

# Run Netgen LVS comparison
if [ -f "{design_name}.spice" ]; then
    export NETGEN_COLUMNS=80
    netgen -batch lvs "{design_name}.spice {design_name}" "{gl_netlist} {design_name}" \\
        {netgen_setup} {lvs_report} -json
    
    # Check LVS result
    if grep -q "Circuits match uniquely" {lvs_report} 2>/dev/null; then
        echo "LVS_RESULT: MATCH"
    else
        echo "LVS_RESULT: MISMATCH"
        cat {lvs_report} | tail -50
    fi
else
    echo "LVS_RESULT: EXTRACTION_FAILED"
    echo "Failed to extract SPICE netlist from GDS"
fi
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse LVS results."""
        lvs_report = context.output_dir / f'{context.user_module}_lvs.rpt'
        
        if 'LVS_RESULT: MATCH' in stdout:
            return self._make_passed_result(
                "LVS matched - layout and schematic are equivalent"
            )
        
        if 'LVS_RESULT: MISMATCH' in stdout:
            return self._make_failed_result(
                "LVS mismatch - layout and schematic differ",
                log_path=str(lvs_report) if lvs_report.exists() else None
            )
        
        if 'LVS_RESULT: EXTRACTION_FAILED' in stdout:
            return self._make_failed_result(
                "Failed to extract SPICE netlist from GDS"
            )
        
        if 'Circuits match uniquely' in stdout:
            return self._make_passed_result("LVS matched")
        
        if 'Circuits do not match' in stdout or 'mismatch' in stdout.lower():
            return self._make_failed_result(
                "LVS mismatch",
                log_path=str(lvs_report) if lvs_report.exists() else None
            )
        
        if returncode != 0:
            # Extract meaningful error from stderr
            error_msg = "LVS check failed"
            if 'command not found' in stderr:
                error_msg = "Required tool not found in container"
            elif stderr.strip():
                lines = stderr.strip().split('\n')
                for line in lines:
                    if 'error' in line.lower() or 'fail' in line.lower():
                        error_msg = line.strip()[:100]
                        break
            
            return self._make_failed_result(error_msg)
        
        return self._make_passed_result("LVS completed")
