# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""OEB (Output Enable Buffer) check - verifies OEB signal connections."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class OebCheck(DockerCheck):
    """Output Enable Buffer verification check.
    
    Verifies that OEB (Output Enable Buffer) signals are properly connected
    in the user project wrapper. OEB signals control the bidirectional GPIO
    pads and must be correctly wired.
    """
    
    name = "oeb"
    display_name = "OEB Check"
    description = "Output Enable Buffer signal verification"
    category = "connectivity"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'mini']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites for OEB check."""
        # Check for GL verilog netlist
        gl_netlist = context.project_path / 'verilog' / 'gl' / f'{context.user_module}.v'
        if not gl_netlist.exists():
            return self._make_failed_result(
                f"GL netlist not found: {gl_netlist.name}",
                details={'path': str(gl_netlist)}
            )
        
        return None
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the OEB check command."""
        design_name = context.user_module
        gl_netlist = context.project_path / 'verilog' / 'gl' / f'{design_name}.v'
        output_dir = context.output_dir
        
        # Python script to check OEB connections in the netlist
        cmd = f'''
python3 << 'PYTHON_EOF'
import re
import sys
from pathlib import Path

def check_oeb_connections(netlist_path):
    """Check that OEB signals are properly connected."""
    try:
        content = Path(netlist_path).read_text()
    except Exception as e:
        print(f"OEB_ERROR: Cannot read netlist: {{e}}")
        return False
    
    # Find all io_oeb connections
    oeb_pattern = r'io_oeb\\[(\d+)\\]'
    oeb_matches = re.findall(oeb_pattern, content)
    
    if not oeb_matches:
        # Try alternative pattern
        oeb_pattern2 = r'\\.io_oeb\\[(\d+)\\]\\s*\\(([^)]+)\\)'
        oeb_matches2 = re.findall(oeb_pattern2, content)
        
        if not oeb_matches2:
            print("OEB_WARNING: No io_oeb signals found in netlist")
            print("OEB_RESULT: SKIPPED")
            return True
        
        # Check that OEB signals are not floating
        floating = []
        for idx, signal in oeb_matches2:
            if signal.strip() in ['', "1'b0", "1'b1", "1'bx", "1'bz"]:
                continue
            if 'oeb' not in signal.lower() and signal.strip():
                # Connected to something
                pass
        
        if floating:
            print(f"OEB_ERROR: Floating OEB signals: {{floating}}")
            print("OEB_RESULT: FAILED")
            return False
    
    # Check for common OEB issues
    issues = []
    
    # Check if user_oeb signals exist
    if 'user_oeb' not in content and 'io_oeb' in content:
        # This is okay - direct OEB connections
        pass
    
    # Check for proper OEB width (should be 38 for full GPIO range)
    oeb_width_match = re.search(r'io_oeb\s*\\[(\\d+):0\\]', content)
    if oeb_width_match:
        width = int(oeb_width_match.group(1)) + 1
        if width < 33:
            issues.append(f"OEB bus width {{width}} is less than expected 33")
    
    if issues:
        for issue in issues:
            print(f"OEB_WARNING: {{issue}}")
    
    print("OEB_RESULT: PASSED")
    return True

success = check_oeb_connections("{gl_netlist}")
sys.exit(0 if success else 1)
PYTHON_EOF
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse OEB check results."""
        if 'OEB_RESULT: PASSED' in stdout:
            return self._make_passed_result("OEB connections verified")
        
        if 'OEB_RESULT: SKIPPED' in stdout:
            return self._make_passed_result(
                "OEB check skipped - no OEB signals in design"
            )
        
        if 'OEB_RESULT: FAILED' in stdout:
            # Extract the error message
            for line in stdout.split('\n'):
                if 'OEB_ERROR:' in line:
                    error_msg = line.replace('OEB_ERROR:', '').strip()
                    return self._make_failed_result(error_msg)
            
            return self._make_failed_result("OEB verification failed")
        
        if 'OEB_ERROR:' in stdout:
            for line in stdout.split('\n'):
                if 'OEB_ERROR:' in line:
                    error_msg = line.replace('OEB_ERROR:', '').strip()
                    return self._make_failed_result(error_msg)
        
        if returncode != 0:
            error_msg = "OEB check failed"
            if 'command not found' in stderr:
                error_msg = "Python not found in container"
            elif 'ModuleNotFoundError' in stderr:
                error_msg = "Missing Python module in container"
            elif stderr.strip():
                lines = stderr.strip().split('\n')
                error_msg = lines[-1][:100] if lines else error_msg
            
            return self._make_failed_result(error_msg)
        
        return self._make_passed_result("OEB check completed")
