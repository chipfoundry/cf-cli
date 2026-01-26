# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Check configuration and context management."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List


@dataclass
class CheckContext:
    """Context passed to each check during execution.
    
    Contains all the information a check needs to run, including
    project paths, PDK information, and output directories.
    """
    # Project information
    project_path: Path
    pdk: str
    pdk_path: Path
    project_type: str  # 'digital', 'analog', 'openframe', 'mini'
    
    # Output directories
    output_dir: Path
    log_dir: Path
    report_dir: Path
    
    # Project module info (derived from project type)
    user_module: str
    
    # Optional caravel root for golden wrapper comparison
    caravel_root: Optional[Path] = None
    
    # Additional configuration options
    extra_config: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_project(cls, 
                     project_path: Path,
                     pdk: str,
                     pdk_path: Path,
                     project_type: str,
                     output_dir: Optional[Path] = None,
                     caravel_root: Optional[Path] = None) -> 'CheckContext':
        """Create a CheckContext from project information.
        
        Args:
            project_path: Path to the project root
            pdk: PDK name (e.g., 'sky130A')
            pdk_path: Path to the PDK installation
            project_type: Project type ('digital', 'analog', 'openframe', 'mini')
            output_dir: Optional output directory (defaults to project_path/precheck_results)
            caravel_root: Optional path to caravel/golden wrapper
            
        Returns:
            Configured CheckContext instance
        """
        # Determine user module based on project type
        user_module_map = {
            'digital': 'user_project_wrapper',
            'analog': 'user_analog_project_wrapper',
            'openframe': 'openframe_project_wrapper',
            'mini': 'user_project_wrapper_mini4',
        }
        user_module = user_module_map.get(project_type, 'user_project_wrapper')
        
        # Set up output directories
        if output_dir is None:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            output_dir = project_path / 'precheck_results' / timestamp
        
        log_dir = output_dir / 'logs'
        report_dir = output_dir / 'reports'
        
        return cls(
            project_path=project_path,
            pdk=pdk,
            pdk_path=pdk_path,
            project_type=project_type,
            output_dir=output_dir,
            log_dir=log_dir,
            report_dir=report_dir,
            user_module=user_module,
            caravel_root=caravel_root,
        )
    
    def ensure_dirs(self):
        """Create output directories if they don't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
    
    def get_gds_path(self) -> Path:
        """Get path to the project's GDS file."""
        return self.project_path / 'gds' / f'{self.user_module}.gds'
    
    def get_user_defines_path(self) -> Path:
        """Get path to user_defines.v file."""
        return self.project_path / 'verilog' / 'rtl' / 'user_defines.v'
    
    def get_lvs_config_path(self) -> Path:
        """Get path to LVS configuration file."""
        return self.project_path / 'lvs' / self.user_module / 'lvs_config.json'


@dataclass
class PrecheckConfig:
    """Global precheck configuration.
    
    Controls which checks to run, output formats, and execution options.
    """
    # Check selection
    checks: Optional[List[str]] = None  # None = run all
    skip_checks: Optional[List[str]] = None
    categories: Optional[List[str]] = None
    
    # Execution options
    native_only: bool = False
    docker_image: str = 'chipfoundry/mpw_precheck:latest'
    
    # Output options
    json_output: bool = False
    json_log_path: Optional[Path] = None
    verbose: bool = False
    
    # Dry run
    dry_run: bool = False
    
    def should_run_check(self, check_name: str, check_category: str, requires_docker: bool) -> bool:
        """Determine if a specific check should be run.
        
        Args:
            check_name: Name of the check
            check_category: Category of the check
            requires_docker: Whether the check requires Docker
            
        Returns:
            True if the check should be run
        """
        # Skip if native_only and check requires Docker
        if self.native_only and requires_docker:
            return False
        
        # Skip if explicitly in skip list
        if self.skip_checks and check_name in self.skip_checks:
            return False
        
        # If specific checks are requested, only run those
        if self.checks:
            return check_name in self.checks
        
        # If categories are specified, only run checks in those categories
        if self.categories:
            return check_category in self.categories
        
        # Default: run the check
        return True
