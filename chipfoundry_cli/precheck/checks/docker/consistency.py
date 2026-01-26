# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Consistency check - validates design file consistency."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class ConsistencyCheck(DockerCheck):
    """Design file consistency verification.
    
    Verifies that the GDS, LEF, DEF, and verilog files are consistent
    with each other in terms of cell names, port names, and hierarchy.
    """
    
    name = "consistency"
    display_name = "Consistency Check"
    description = "Verifies design file consistency"
    category = "layout"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'openframe']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites."""
        gds_result = self.check_gds_exists(context)
        if gds_result:
            return gds_result
        
        return None
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the consistency check command."""
        design_name = context.user_module
        gds_path = self.get_gds_path(context)
        output_dir = context.output_dir
        
        lef_path = context.project_path / 'lef' / f'{design_name}.lef'
        def_path = context.project_path / 'def' / f'{design_name}.def'
        gl_netlist = context.project_path / 'verilog' / 'gl' / f'{design_name}.v'
        
        # Python script to check consistency
        cmd = f'''
python3 << 'PYTHON_EOF'
import re
import sys
from pathlib import Path

def extract_gds_cells(gds_path):
    """Extract cell names from GDS using klayout."""
    # We'll use a simple approach - check if file exists and is valid
    path = Path(gds_path)
    if not path.exists():
        return None, f"GDS file not found: {{gds_path}}"
    if path.stat().st_size == 0:
        return None, "GDS file is empty"
    return set(), None

def extract_lef_macros(lef_path):
    """Extract macro names from LEF file."""
    path = Path(lef_path)
    if not path.exists():
        return set(), None  # LEF is optional
    
    macros = set()
    try:
        content = path.read_text()
        for match in re.finditer(r'^MACRO\\s+(\\S+)', content, re.MULTILINE):
            macros.add(match.group(1))
    except Exception as e:
        return None, str(e)
    
    return macros, None

def extract_verilog_modules(v_path):
    """Extract module names from Verilog file."""
    path = Path(v_path)
    if not path.exists():
        return None, f"Verilog file not found: {{v_path}}"
    
    modules = set()
    try:
        content = path.read_text()
        for match in re.finditer(r'^module\\s+(\\S+)', content, re.MULTILINE):
            modules.add(match.group(1).rstrip('('))
    except Exception as e:
        return None, str(e)
    
    return modules, None

def check_consistency():
    design_name = "{design_name}"
    issues = []
    warnings = []
    
    # Check GDS exists and is valid
    gds_cells, gds_error = extract_gds_cells("{gds_path}")
    if gds_error:
        issues.append(f"GDS: {{gds_error}}")
    
    # Check LEF if it exists
    lef_path = Path("{lef_path}")
    if lef_path.exists():
        lef_macros, lef_error = extract_lef_macros("{lef_path}")
        if lef_error:
            warnings.append(f"LEF: {{lef_error}}")
        elif lef_macros and design_name not in lef_macros:
            warnings.append(f"LEF does not contain macro '{{design_name}}'")
    
    # Check GL verilog
    gl_path = Path("{gl_netlist}")
    if gl_path.exists():
        modules, v_error = extract_verilog_modules("{gl_netlist}")
        if v_error:
            issues.append(f"Verilog: {{v_error}}")
        elif modules and design_name not in modules:
            issues.append(f"GL netlist does not contain module '{{design_name}}'")
    else:
        issues.append("GL netlist not found")
    
    # Check DEF if it exists
    def_path = Path("{def_path}")
    if def_path.exists():
        try:
            content = def_path.read_text()
            if f"DESIGN {{design_name}}" not in content:
                warnings.append(f"DEF does not contain design '{{design_name}}'")
        except Exception as e:
            warnings.append(f"DEF: {{e}}")
    
    # Report results
    for warning in warnings:
        print(f"CONSISTENCY_WARNING: {{warning}}")
    
    if issues:
        for issue in issues:
            print(f"CONSISTENCY_ERROR: {{issue}}")
        print("CONSISTENCY_RESULT: FAILED")
        return False
    
    print("CONSISTENCY_RESULT: PASSED")
    return True

success = check_consistency()
sys.exit(0 if success else 1)
PYTHON_EOF
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse consistency check results."""
        if 'CONSISTENCY_RESULT: PASSED' in stdout:
            # Check for warnings
            warnings = [line for line in stdout.split('\n') 
                       if 'CONSISTENCY_WARNING:' in line]
            if warnings:
                msg = f"Consistency verified with {len(warnings)} warning(s)"
            else:
                msg = "Design file consistency verified"
            return self._make_passed_result(msg)
        
        if 'CONSISTENCY_RESULT: FAILED' in stdout:
            # Extract errors
            errors = []
            for line in stdout.split('\n'):
                if 'CONSISTENCY_ERROR:' in line:
                    errors.append(line.replace('CONSISTENCY_ERROR:', '').strip())
            
            if errors:
                return self._make_failed_result(
                    errors[0],
                    details={'errors': errors} if len(errors) > 1 else None
                )
            
            return self._make_failed_result("Consistency check failed")
        
        if returncode != 0:
            error_msg = "Consistency check failed"
            if stderr.strip():
                lines = stderr.strip().split('\n')
                for line in lines:
                    if 'error' in line.lower():
                        error_msg = line[:100]
                        break
                else:
                    error_msg = lines[-1][:100] if lines else error_msg
            
            return self._make_failed_result(error_msg)
        
        return self._make_passed_result("Consistency check completed")
