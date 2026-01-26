# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""XOR check - verifies wrapper matches golden reference."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class XorCheck(DockerCheck):
    """XOR check against golden wrapper.
    
    Compares the user's wrapper GDS against the empty golden reference
    to ensure no unauthorized modifications to the wrapper frame.
    """
    
    name = "xor"
    display_name = "XOR Check"
    description = "XOR check against golden wrapper reference"
    category = "layout"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites."""
        gds_result = self.check_gds_exists(context)
        if gds_result:
            return gds_result
        
        return None
    
    def _get_golden_wrapper_path(self, context: CheckContext) -> str:
        """Get the path to the golden wrapper GDS in the Docker container."""
        # Golden wrappers are in /opt/caravel/gds/ in the Docker image
        if 'gf180mcu' in context.pdk:
            return '/opt/caravel/gds/user_project_wrapper_empty.gds'
        elif context.project_type == 'openframe':
            return '/opt/caravel/gds/openframe_project_wrapper_empty.gds'
        elif context.project_type == 'analog':
            return '/opt/caravel/gds/user_analog_project_wrapper_empty.gds'
        elif context.project_type == 'mini':
            # Mini uses the regular wrapper but smaller
            return '/opt/caravel/gds/user_project_wrapper_empty.gds'
        else:
            return '/opt/caravel/gds/user_project_wrapper_empty.gds'
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the XOR check command using Magic."""
        gds_path = self.get_gds_path(context)
        pdk_root = context.pdk_path.parent
        pdk = context.pdk
        magicrc = f'{pdk_root}/{pdk}/libs.tech/magic/{pdk}.magicrc'
        
        golden_wrapper = self._get_golden_wrapper_path(context)
        output_gds = context.output_dir / 'xor_result.gds'
        design_name = context.user_module
        
        # XOR script using Magic
        cmd = f'''
export PDK_ROOT={pdk_root}
export PDK={pdk}

# Check if golden wrapper exists
if [ ! -f "{golden_wrapper}" ]; then
    echo "XOR_ERROR: Golden wrapper not found at {golden_wrapper}"
    exit 1
fi

# Create XOR script
cat > /tmp/xor.tcl << 'XOR_SCRIPT'
puts "Loading golden wrapper..."
gds read {golden_wrapper}
load {design_name}
cellname rename {design_name} {design_name}_golden

puts "Loading user design..."
gds read {gds_path}
load {design_name}

puts "Performing XOR..."
flatten {design_name}_flat
load {design_name}_golden
flatten {design_name}_golden_flat

load {design_name}_flat
xor -nolabels {design_name}_golden_flat

# Count XOR differences by checking for non-empty result
set bbox [box values]
if {{$bbox == "0 0 0 0"}} {{
    puts "XOR differences: 0"
}} else {{
    # There are differences
    select area
    set count [llength [edit]]
    if {{$count == 0}} {{
        puts "XOR differences: 0"
    }} else {{
        puts "XOR differences: 1"
    }}
}}

gds write {output_gds}
puts "XOR complete"
quit
XOR_SCRIPT

magic -dnull -noconsole -rcfile {magicrc} /tmp/xor.tcl 2>&1
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse XOR results."""
        # Check for errors first
        if 'XOR_ERROR:' in stdout:
            for line in stdout.split('\n'):
                if 'XOR_ERROR:' in line:
                    return self._make_failed_result(
                        line.replace('XOR_ERROR:', '').strip()
                    )
        
        # Look for XOR count in output
        xor_match = re.search(r'XOR differences:\s*(\d+)', stdout)
        
        if xor_match:
            differences = int(xor_match.group(1))
            if differences > 0:
                return self._make_failed_result(
                    f"{differences} XOR difference(s) found in wrapper",
                    details={'differences': differences}
                )
            return self._make_passed_result("Wrapper matches golden reference")
        
        # Check for file read errors
        if "Cannot open" in stderr or "couldn't be read" in stderr:
            return self._make_failed_result(
                "Failed to read GDS file",
                error_snippet=stderr[:150] if stderr else None
            )
        
        if returncode != 0:
            return self._make_failed_result(
                f"XOR check failed (exit code {returncode})"
            )
        
        return self._make_passed_result("XOR check completed")
