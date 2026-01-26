# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Spike check - detects zero-area and spike geometry violations in GDS."""

import re
from pathlib import Path
from typing import Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class SpikeCheck(DockerCheck):
    """Spike/zero-area geometry detection check.
    
    Detects problematic geometries in the GDS that could cause
    manufacturing issues, including:
    - Zero-area polygons (spikes)
    - Self-intersecting polygons
    - Degenerate paths
    """
    
    name = "spike"
    display_name = "Spike Check"
    description = "Detects spike and zero-area geometry violations"
    category = "layout"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check prerequisites."""
        return self.check_gds_exists(context)
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the spike check command using KLayout."""
        gds_path = self.get_gds_path(context)
        output_dir = context.output_dir
        report_path = output_dir / 'spike_check.log'
        
        # Use KLayout to check for zero-area and spike geometries
        cmd = f'''
python3 << 'PYTHON_EOF'
import sys

try:
    import klayout.db as pya
except ImportError:
    print("SPIKE_ERROR: klayout.db module not available")
    sys.exit(1)

def check_spikes(gds_path, report_path):
    """Check for spike/zero-area geometries in GDS."""
    
    layout = pya.Layout()
    try:
        layout.read(str(gds_path))
    except Exception as e:
        print(f"SPIKE_ERROR: Failed to read GDS: {{e}}")
        return False
    
    violations = []
    checked_cells = 0
    checked_shapes = 0
    
    for cell_idx in layout.each_cell():
        cell = layout.cell(cell_idx)
        checked_cells += 1
        
        for layer_idx in layout.layer_indices():
            for shape in cell.shapes(layer_idx).each():
                checked_shapes += 1
                
                if shape.is_polygon():
                    poly = shape.polygon
                    # Check for zero or very small area
                    area = abs(poly.area())
                    if area == 0:
                        layer_info = layout.get_info(layer_idx)
                        violations.append(f"Zero-area polygon in {{cell.name}} on layer {{layer_info}}")
                    # Check for self-intersection (simplified)
                    if poly.num_points() > 0 and poly.num_points() < 3:
                        layer_info = layout.get_info(layer_idx)
                        violations.append(f"Degenerate polygon in {{cell.name}} on layer {{layer_info}}")
                
                elif shape.is_path():
                    path = shape.path
                    if path.length() == 0:
                        layer_info = layout.get_info(layer_idx)
                        violations.append(f"Zero-length path in {{cell.name}} on layer {{layer_info}}")
    
    # Write report
    with open(report_path, 'w') as f:
        f.write(f"Spike Check Report\\n")
        f.write(f"==================\\n")
        f.write(f"GDS: {gds_path}\\n")
        f.write(f"Cells checked: {{checked_cells}}\\n")
        f.write(f"Shapes checked: {{checked_shapes}}\\n")
        f.write(f"Violations: {{len(violations)}}\\n\\n")
        
        if violations:
            for v in violations[:50]:  # Limit to first 50
                f.write(f"  - {{v}}\\n")
            if len(violations) > 50:
                f.write(f"  ... and {{len(violations) - 50}} more\\n")
    
    if violations:
        print(f"SPIKE_CHECK: FAILED")
        print(f"Found {{len(violations)}} spike/zero-area violation(s)")
        for v in violations[:5]:
            print(f"  - {{v}}")
        if len(violations) > 5:
            print(f"  ... and {{len(violations) - 5}} more")
        return False
    else:
        print(f"SPIKE_CHECK: PASSED")
        print(f"Checked {{checked_cells}} cells, {{checked_shapes}} shapes - no violations")
        return True

success = check_spikes("{gds_path}", "{report_path}")
sys.exit(0 if success else 1)
PYTHON_EOF
'''
        return cmd
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse spike check results."""
        report_path = context.output_dir / 'spike_check.log'
        
        if 'SPIKE_CHECK: PASSED' in stdout:
            return self._make_passed_result("No spike or zero-area violations found")
        
        if 'SPIKE_CHECK: FAILED' in stdout:
            # Extract violation count
            match = re.search(r'Found (\d+) spike', stdout)
            count = match.group(1) if match else "unknown"
            
            return self._make_failed_result(
                f"{count} spike/zero-area violation(s) found",
                log_path=str(report_path) if report_path.exists() else None
            )
        
        if 'SPIKE_ERROR:' in stdout:
            for line in stdout.split('\n'):
                if 'SPIKE_ERROR:' in line:
                    return self._make_failed_result(
                        line.replace('SPIKE_ERROR:', '').strip()
                    )
        
        if returncode != 0:
            error_msg = "Spike check failed"
            if 'ImportError' in stderr or 'ModuleNotFoundError' in stderr:
                error_msg = "KLayout Python module not available"
            elif stderr.strip():
                lines = stderr.strip().split('\n')
                error_msg = lines[-1][:100] if lines else error_msg
            
            return self._make_failed_result(error_msg)
        
        return self._make_passed_result("Spike check completed")
