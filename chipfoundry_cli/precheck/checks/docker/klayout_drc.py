# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""KLayout DRC checks - multiple DRC rule checks using KLayout."""

import re
from pathlib import Path
from typing import List, Optional

from chipfoundry_cli.precheck.checks.docker.base_docker import DockerCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


class KlayoutDrcCheck(DockerCheck):
    """Base class for KLayout DRC checks."""
    
    category = "drc"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog', 'openframe', 'mini']
    
    # Override in subclasses
    drc_script: str = ""
    klayout_args: List[str] = []
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Check that GDS file exists."""
        return self.check_gds_exists(context)
    
    def get_docker_command(self, context: CheckContext) -> str:
        """Build the KLayout DRC command."""
        gds_path = self.get_gds_path(context)
        report_path = context.report_dir / f'{self.name}.xml'
        
        # Get DRC script path based on PDK
        drc_script = self._get_drc_script_path(context)
        
        # Build klayout command
        args = ' '.join(self.klayout_args)
        
        cmd = (
            f"klayout -b -r {drc_script} "
            f"-rd input={gds_path} "
            f"-rd report={report_path} "
            f"{args}"
        )
        
        return cmd
    
    def _get_drc_script_path(self, context: CheckContext) -> str:
        """Get the DRC script path for the current PDK."""
        assets_path = Path(__file__).parent.parent.parent / 'assets' / 'tech_files'
        return str(assets_path / f'{context.pdk}_mr.drc')
    
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse KLayout DRC results."""
        if returncode != 0:
            # Exit code 125 = Docker daemon error
            if returncode == 125:
                error_msg = stderr[:200] if stderr else "Docker container failed to start"
                return self._make_failed_result(
                    f"Docker error: {error_msg}",
                    details={'returncode': returncode, 'stderr': stderr[:500]}
                )
            # Check for specific error patterns
            if 'Error' in stderr or 'error' in stderr.lower():
                return self._make_failed_result(
                    "DRC check failed with errors",
                    details={'stderr': stderr[:500]}
                )
            return self._make_failed_result(
                f"DRC check failed (exit code {returncode})",
                details={'returncode': returncode, 'stderr': stderr[:300] if stderr else None}
            )
        
        # Check for violations in output
        violation_match = re.search(r'(\d+)\s+violations?', stdout, re.IGNORECASE)
        if violation_match:
            violations = int(violation_match.group(1))
            if violations > 0:
                return self._make_failed_result(
                    f"{violations} DRC violations found",
                    details={'violations': violations}
                )
        
        return self._make_passed_result("No DRC violations")


@register_check
class KlayoutFeolCheck(KlayoutDrcCheck):
    """KLayout Front-End-Of-Line DRC check."""
    
    name = "klayout_feol"
    display_name = "Klayout FEOL DRC"
    description = "Front-end-of-line design rule check using KLayout"
    klayout_args = ['-rd', 'feol=true']
    
    def _get_klayout_args(self, context: CheckContext) -> List[str]:
        args = ['-rd', 'feol=true']
        if 'gf180mcuC' in context.pdk:
            args.extend(['-rd', 'metal_top=9K', '-rd', 'mim_option=B', 
                        '-rd', 'metal_level=5LM', '-rd', 'conn_drc=true'])
        elif 'gf180mcuD' in context.pdk:
            args.extend(['-rd', 'metal_top=11K', '-rd', 'mim_option=B',
                        '-rd', 'metal_level=5LM', '-rd', 'conn_drc=true',
                        '-rd', 'run_mode=deep', '-rd', 'density=false',
                        '-rd', 'split_deep=false', '-rd', 'slow_via=false'])
        return args


@register_check
class KlayoutBeolCheck(KlayoutDrcCheck):
    """KLayout Back-End-Of-Line DRC check."""
    
    name = "klayout_beol"
    display_name = "Klayout BEOL DRC"
    description = "Back-end-of-line design rule check using KLayout"
    klayout_args = ['-rd', 'beol=true']
    
    def _get_klayout_args(self, context: CheckContext) -> List[str]:
        args = ['-rd', 'beol=true']
        if 'gf180mcuC' in context.pdk:
            args.extend(['-rd', 'metal_top=9K', '-rd', 'mim_option=B',
                        '-rd', 'metal_level=5LM', '-rd', 'conn_drc=true'])
        elif 'gf180mcuD' in context.pdk:
            args.extend(['-rd', 'metal_top=11K', '-rd', 'mim_option=B',
                        '-rd', 'metal_level=5LM', '-rd', 'conn_drc=true',
                        '-rd', 'run_mode=deep', '-rd', 'density=false',
                        '-rd', 'split_deep=false', '-rd', 'slow_via=false'])
        return args


@register_check
class KlayoutOffgridCheck(KlayoutDrcCheck):
    """KLayout off-grid check."""
    
    name = "klayout_offgrid"
    display_name = "Klayout Offgrid"
    description = "Off-grid geometry check using KLayout"
    klayout_args = ['-rd', 'offgrid=true']
    
    def _get_klayout_args(self, context: CheckContext) -> List[str]:
        args = ['-rd', 'offgrid=true']
        if 'gf180mcuC' in context.pdk:
            args.extend(['-rd', 'metal_top=9K', '-rd', 'mim_option=B',
                        '-rd', 'metal_level=5LM', '-rd', 'conn_drc=true'])
        elif 'gf180mcuD' in context.pdk:
            args.extend(['-rd', 'metal_top=11K', '-rd', 'mim_option=B',
                        '-rd', 'metal_level=5LM', '-rd', 'conn_drc=true',
                        '-rd', 'run_mode=deep', '-rd', 'density=false',
                        '-rd', 'split_deep=false', '-rd', 'slow_via=false'])
        return args


@register_check  
class KlayoutDensityCheck(KlayoutDrcCheck):
    """KLayout metal minimum clear area density check."""
    
    name = "klayout_density"
    display_name = "Klayout Density"
    description = "Metal minimum clear area density check"
    
    def _get_drc_script_path(self, context: CheckContext) -> str:
        assets_path = Path(__file__).parent.parent.parent / 'assets' / 'tech_files'
        if 'gf180mcu' in context.pdk:
            return str(assets_path / 'gf180mcu_density.lydrc')
        return str(assets_path / 'met_min_ca_density.lydrc')


@register_check
class KlayoutPinLabelCheck(KlayoutDrcCheck):
    """KLayout pin label purposes overlapping drawing check."""
    
    name = "klayout_pin_label"
    display_name = "Klayout Pin Label"
    description = "Pin label purposes overlapping drawing check"
    supported_pdks = ['sky130A', 'sky130B']
    
    def get_docker_command(self, context: CheckContext) -> str:
        gds_path = self.get_gds_path(context)
        report_path = context.report_dir / f'{self.name}.xml'
        assets_path = Path(__file__).parent.parent.parent / 'assets' / 'tech_files'
        drc_script = assets_path / 'pin_label_purposes_overlapping_drawing.rb.drc'
        
        cmd = (
            f"klayout -b -r {drc_script} "
            f"-rd input={gds_path} "
            f"-rd report={report_path} "
            f"-rd top_cell_name={context.user_module}"
        )
        return cmd


@register_check
class KlayoutZeroareaCheck(KlayoutDrcCheck):
    """KLayout zero area polygon check."""
    
    name = "klayout_zeroarea"
    display_name = "Klayout ZeroArea"
    description = "Zero area polygon check"
    supported_pdks = ['sky130A', 'sky130B']
    
    def get_docker_command(self, context: CheckContext) -> str:
        gds_path = self.get_gds_path(context)
        output_gds = context.output_dir / f'{gds_path.stem}_no_zero_areas.gds'
        assets_path = Path(__file__).parent.parent.parent / 'assets' / 'tech_files'
        drc_script = assets_path / 'zeroarea.rb.drc'
        
        cmd = (
            f"klayout -b -r {drc_script} "
            f"-rd input={gds_path} "
            f"-rd cleaned_output={output_gds}"
        )
        return cmd
