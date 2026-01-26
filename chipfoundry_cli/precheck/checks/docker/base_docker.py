# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Base class for Docker-based checks."""

import os
import shutil
import subprocess
import tempfile
from abc import abstractmethod
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from chipfoundry_cli.precheck.checks.base import BaseCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus


class DockerCheck(BaseCheck):
    """Base class for checks that require Docker.
    
    Provides common functionality for running EDA tools inside
    a Docker container.
    """
    
    requires_docker = True
    docker_image = 'chipfoundry/mpw_precheck:latest'
    
    def run(self, context: CheckContext) -> CheckResult:
        """Execute the check inside Docker.
        
        Args:
            context: Check execution context
            
        Returns:
            CheckResult indicating the check outcome
        """
        # Check if Docker is available
        if not self._is_docker_available():
            return self._make_error_result("Docker not found")
        
        # Prepare the check
        try:
            prep_result = self.prepare(context)
            if prep_result is not None:
                return prep_result
        except Exception as e:
            return self._make_error_result(f"Preparation failed: {e}")
        
        # Build and run Docker command
        try:
            docker_cmd = self._build_docker_command(context)
            check_cmd = self.get_docker_command(context)
            
            # Run the container
            returncode, stdout, stderr = self._run_docker(
                docker_cmd, 
                check_cmd,
                context
            )
            
            # Save output to log file
            log_path = self._save_log(context, stdout, stderr, returncode)
            
            # Parse and return result
            result = self.parse_result(returncode, stdout, stderr, context)
            
            # Attach log path if check failed
            if result.status in (CheckStatus.FAILED, CheckStatus.ERROR):
                result.log_path = log_path
                # Extract error snippet from stderr or stdout
                if stderr and stderr.strip():
                    result.error_snippet = self._extract_error_snippet(stderr)
                elif stdout and 'error' in stdout.lower():
                    result.error_snippet = self._extract_error_snippet(stdout)
            
            return result
            
        except Exception as e:
            return self._make_error_result(f"Docker execution failed: {e}")
    
    def prepare(self, context: CheckContext) -> Optional[CheckResult]:
        """Prepare for the check execution.
        
        Override this method to perform any pre-check validation
        or file preparation.
        
        Args:
            context: Check execution context
            
        Returns:
            None if preparation succeeded, or a CheckResult if it failed
        """
        return None
    
    @abstractmethod
    def get_docker_command(self, context: CheckContext) -> str:
        """Get the command to run inside the Docker container.
        
        Args:
            context: Check execution context
            
        Returns:
            Shell command string to execute in the container
        """
        pass
    
    @abstractmethod
    def parse_result(self, returncode: int, stdout: str, stderr: str,
                     context: CheckContext) -> CheckResult:
        """Parse the Docker execution result.
        
        Args:
            returncode: Exit code from Docker
            stdout: Standard output
            stderr: Standard error
            context: Check execution context
            
        Returns:
            CheckResult based on the execution output
        """
        pass
    
    def _is_docker_available(self) -> bool:
        """Check if Docker is available on the system."""
        return shutil.which('docker') is not None
    
    def _build_docker_command(self, context: CheckContext) -> List[str]:
        """Build the base Docker run command.
        
        Args:
            context: Check execution context
            
        Returns:
            List of Docker command arguments
        """
        user_id = os.getuid()
        group_id = os.getgid()
        
        # Get the precheck assets path for tech files
        assets_path = Path(__file__).parent.parent.parent / 'assets'
        
        cmd = [
            'docker', 'run', '--rm',
            '-v', f'{context.project_path}:{context.project_path}',
            '-v', f'{context.pdk_path.parent}:{context.pdk_path.parent}',
            '-v', f'{context.output_dir}:{context.output_dir}',
            '-e', f'INPUT_DIRECTORY={context.project_path}',
            '-e', f'PDK_PATH={context.pdk_path}',
            '-e', f'PDK_ROOT={context.pdk_path.parent}',
            '-e', f'PDKPATH={context.pdk_path}',
            '-u', f'{user_id}:{group_id}',
        ]
        
        # Add caravel root if available
        if context.caravel_root:
            cmd.extend([
                '-v', f'{context.caravel_root}:{context.caravel_root}',
                '-e', f'GOLDEN_CARAVEL={context.caravel_root}',
            ])
        
        # Add assets path if it exists
        if assets_path.exists():
            cmd.extend(['-v', f'{assets_path}:{assets_path}'])
        
        cmd.append(self.docker_image)
        cmd.extend(['bash', '-c'])
        
        return cmd
    
    # Default timeout for Docker commands (10 minutes)
    docker_timeout: int = 600
    
    def _run_docker(self, docker_cmd: List[str], check_cmd: str,
                    context: CheckContext) -> Tuple[int, str, str]:
        """Run a command inside Docker.
        
        Args:
            docker_cmd: Base Docker command
            check_cmd: Command to run inside container
            context: Check execution context
            
        Returns:
            Tuple of (returncode, stdout, stderr)
        """
        full_cmd = docker_cmd + [check_cmd]
        
        # Ensure output directories exist
        context.ensure_dirs()
        
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                cwd=str(context.project_path),
                timeout=self.docker_timeout,
            )
            
            # Log command for debugging if verbose
            if result.returncode == 125:
                # Docker daemon error - include more details
                import logging
                logging.debug(f"Docker command failed: {' '.join(full_cmd[:10])}...")
                logging.debug(f"stderr: {result.stderr}")
            
            return result.returncode, result.stdout, result.stderr
        
        except subprocess.TimeoutExpired:
            return -1, "", f"Docker command timed out after {self.docker_timeout} seconds"
    
    def get_gds_path(self, context: CheckContext) -> Path:
        """Get the path to the GDS file.
        
        Args:
            context: Check execution context
            
        Returns:
            Path to the GDS file
        """
        return context.project_path / 'gds' / f'{context.user_module}.gds'
    
    def check_gds_exists(self, context: CheckContext) -> Optional[CheckResult]:
        """Check if the GDS file exists.
        
        Args:
            context: Check execution context
            
        Returns:
            CheckResult if GDS doesn't exist, None otherwise
        """
        gds_path = self.get_gds_path(context)
        if not gds_path.exists():
            # Check for compressed version
            gds_gz_path = gds_path.with_suffix('.gds.gz')
            if not gds_gz_path.exists():
                return self._make_failed_result(
                    f"GDS file not found: {gds_path.name}",
                    details={'searched': [str(gds_path), str(gds_gz_path)]}
                )
        return None
    
    def _save_log(self, context: CheckContext, stdout: str, stderr: str, 
                  returncode: int) -> str:
        """Save check output to a log file.
        
        Args:
            context: Check execution context
            stdout: Standard output
            stderr: Standard error
            returncode: Exit code
            
        Returns:
            Path to the log file
        """
        log_dir = context.output_dir / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f'{self.name}.log'
        
        with open(log_path, 'w') as f:
            f.write(f"Check: {self.display_name}\n")
            f.write(f"Exit code: {returncode}\n")
            f.write("=" * 60 + "\n")
            if stdout:
                f.write("STDOUT:\n")
                f.write(stdout)
                f.write("\n")
            if stderr:
                f.write("STDERR:\n")
                f.write(stderr)
                f.write("\n")
        
        return str(log_path)
    
    def _extract_error_snippet(self, output: str, max_length: int = 150) -> str:
        """Extract a meaningful error snippet from output.
        
        Args:
            output: Output text to extract from
            max_length: Maximum length of snippet
            
        Returns:
            A clean error snippet
        """
        lines = output.strip().split('\n')
        
        # Look for lines containing 'error', 'fail', 'cannot', 'not found'
        error_keywords = ['error', 'fail', 'cannot', 'not found', 'mismatch', 
                         'violation', 'invalid', 'missing']
        
        for line in lines:
            line_lower = line.lower()
            if any(kw in line_lower for kw in error_keywords):
                snippet = line.strip()
                if len(snippet) > max_length:
                    return snippet[:max_length] + "..."
                return snippet
        
        # If no error line found, return first non-empty line
        for line in lines:
            if line.strip():
                snippet = line.strip()
                if len(snippet) > max_length:
                    return snippet[:max_length] + "..."
                return snippet
        
        return output[:max_length] + "..." if len(output) > max_length else output
