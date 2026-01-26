# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""
Runner for mpw_precheck inside Docker.

Executes the original mpw_precheck.py script inside a Docker container
with proper volume mounts and environment variables.
"""

import os
import subprocess
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any
from dataclasses import dataclass


# Checks to skip by default (removed from cf-cli precheck)
DEFAULT_SKIP_CHECKS = ['license', 'makefile', 'default', 'documentation']


@dataclass
class PrecheckConfig:
    """Configuration for running precheck."""
    project_path: Path
    pdk_path: Path
    pdk: str
    caravel_root: Path
    output_directory: Optional[Path] = None
    checks: Optional[List[str]] = None
    skip_checks: Optional[List[str]] = None
    private: bool = False
    docker_image: str = 'chipfoundry/mpw_precheck:latest'
    
    def __post_init__(self):
        if self.output_directory is None:
            tag = datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')
            self.output_directory = self.project_path / 'precheck_results' / tag


class PrecheckRunner:
    """Runs mpw_precheck inside Docker with real-time output streaming."""
    
    def __init__(self, 
                 config: PrecheckConfig,
                 mpw_precheck_path: Optional[Path] = None,
                 on_line: Optional[Callable[[str], None]] = None):
        """Initialize the runner.
        
        Args:
            config: Precheck configuration
            mpw_precheck_path: Path to mpw_precheck directory (auto-detected if None)
            on_line: Callback for each line of output
        """
        self.config = config
        self.on_line = on_line
        
        # Find mpw_precheck directory
        if mpw_precheck_path:
            self.mpw_precheck_path = mpw_precheck_path
        else:
            # Look in package directory
            self.mpw_precheck_path = self._find_mpw_precheck()
        
        if not self.mpw_precheck_path or not self.mpw_precheck_path.exists():
            raise FileNotFoundError(
                "mpw_precheck directory not found. "
                "It should be bundled with cf-cli."
            )
    
    def _find_mpw_precheck(self) -> Optional[Path]:
        """Find the mpw_precheck directory."""
        # Check relative to this file (in package)
        pkg_path = Path(__file__).parent.parent.parent / 'mpw_precheck'
        if pkg_path.exists():
            return pkg_path
        
        # Check in workspace root (for development)
        workspace_path = Path(__file__).parent.parent.parent.parent / 'mpw_precheck'
        if workspace_path.exists():
            return workspace_path
        
        return None
    
    def check_docker(self) -> bool:
        """Check if Docker is available and running."""
        if not shutil.which('docker'):
            return False
        
        try:
            result = subprocess.run(
                ['docker', 'info'],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False
    
    def check_image_exists(self) -> bool:
        """Check if the Docker image exists locally."""
        try:
            result = subprocess.run(
                ['docker', 'image', 'inspect', self.config.docker_image],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False
    
    def build_docker_command(self) -> List[str]:
        """Build the Docker run command."""
        user_id = os.getuid()
        group_id = os.getgid()
        
        # Ensure output directory exists
        self.config.output_directory.mkdir(parents=True, exist_ok=True)
        (self.config.output_directory / 'logs').mkdir(parents=True, exist_ok=True)
        (self.config.output_directory / 'outputs').mkdir(parents=True, exist_ok=True)
        (self.config.output_directory / 'outputs' / 'reports').mkdir(parents=True, exist_ok=True)
        
        cmd = [
            'docker', 'run', '--rm',
            '-v', f'{self.config.project_path}:{self.config.project_path}',
            '-v', f'{self.config.pdk_path.parent}:{self.config.pdk_path.parent}',
            '-v', f'{self.config.output_directory}:{self.config.output_directory}',
            '-v', f'{self.mpw_precheck_path}:/precheck',
            '-v', f'{self.config.caravel_root}:{self.config.caravel_root}',
            '-e', f'PDK_ROOT={self.config.pdk_path.parent}',
            '-e', f'PDK_PATH={self.config.pdk_path}',
            '-e', f'PDK={self.config.pdk}',
            '-e', f'GOLDEN_CARAVEL={self.config.caravel_root}',
            '-u', f'{user_id}:{group_id}',
            '-w', '/precheck',
            self.config.docker_image,
        ]
        
        # Build the python command
        python_cmd = [
            'python3', 'mpw_precheck.py',
            '-i', str(self.config.project_path),
            '-p', str(self.config.pdk_path),
            '-o', str(self.config.output_directory),
        ]
        
        # Add specific checks if provided
        if self.config.checks:
            python_cmd.extend(self.config.checks)
        
        # Combine default skip checks with user-provided skip checks
        all_skip_checks = list(DEFAULT_SKIP_CHECKS)
        if self.config.skip_checks:
            for check in self.config.skip_checks:
                if check not in all_skip_checks:
                    all_skip_checks.append(check)
        
        # Add skip checks
        if all_skip_checks:
            python_cmd.append('--skip_checks')
            python_cmd.extend(all_skip_checks)
        
        # Add private flag if needed
        if self.config.private:
            python_cmd.append('--private')
        
        cmd.extend(python_cmd)
        
        return cmd
    
    def run(self) -> int:
        """Run mpw_precheck and return exit code.
        
        Returns:
            Exit code from precheck (0 = success, 2 = failures, other = error)
        """
        cmd = self.build_docker_command()
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        
        # Stream output line by line
        for line in iter(process.stdout.readline, ''):
            if self.on_line:
                self.on_line(line.rstrip())
        
        process.wait()
        return process.returncode


def run_precheck(
    project_path: Path,
    pdk_path: Path,
    pdk: str,
    caravel_root: Path,
    output_directory: Optional[Path] = None,
    checks: Optional[List[str]] = None,
    skip_checks: Optional[List[str]] = None,
    private: bool = False,
    docker_image: str = 'chipfoundry/mpw_precheck:latest',
    on_line: Optional[Callable[[str], None]] = None,
) -> int:
    """Convenience function to run precheck.
    
    Args:
        project_path: Path to project directory
        pdk_path: Path to PDK installation
        pdk: PDK name (e.g., 'sky130A')
        caravel_root: Path to caravel/golden reference
        output_directory: Output directory for results
        checks: Specific checks to run
        skip_checks: Checks to skip
        private: Run private checks (skip open-source checks)
        docker_image: Docker image to use
        on_line: Callback for each line of output
        
    Returns:
        Exit code from precheck
    """
    config = PrecheckConfig(
        project_path=project_path,
        pdk_path=pdk_path,
        pdk=pdk,
        caravel_root=caravel_root,
        output_directory=output_directory,
        checks=checks,
        skip_checks=skip_checks,
        private=private,
        docker_image=docker_image,
    )
    
    runner = PrecheckRunner(config, on_line=on_line)
    return runner.run()
