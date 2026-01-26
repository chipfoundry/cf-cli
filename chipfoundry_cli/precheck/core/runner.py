# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Check runner with hybrid native/Docker execution."""

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, TYPE_CHECKING

from chipfoundry_cli.precheck.core.config import CheckContext, PrecheckConfig
from chipfoundry_cli.precheck.core.logger import PrecheckLogger
from chipfoundry_cli.precheck.core.registry import CheckRegistry, get_registry
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus, PrecheckSummary

if TYPE_CHECKING:
    from chipfoundry_cli.precheck.checks.base import BaseCheck


class CheckRunner:
    """Orchestrates check execution with hybrid native/Docker support.
    
    Native checks run directly in the current Python process.
    Docker checks are batched and run inside a container.
    """
    
    def __init__(self, 
                 registry: Optional[CheckRegistry] = None,
                 logger: Optional[PrecheckLogger] = None):
        """Initialize the check runner.
        
        Args:
            registry: Check registry (uses global if not provided)
            logger: Logger instance (creates default if not provided)
        """
        self.registry = registry or get_registry()
        self.logger = logger
    
    def _check_docker_running(self) -> bool:
        """Check if Docker daemon is running and ready for containers.
        
        Returns:
            True if Docker daemon is accessible and ready, False otherwise
        """
        if not shutil.which('docker'):
            return False
        
        try:
            # Use 'docker ps' instead of 'docker info' - it's more reliable
            # for checking if Docker can actually run containers
            result = subprocess.run(
                ['docker', 'ps', '-q'],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False
    
    def _check_docker_image_exists(self, image: str) -> bool:
        """Check if a Docker image exists locally.
        
        Args:
            image: Docker image name with tag
            
        Returns:
            True if image exists locally, False otherwise
        """
        try:
            result = subprocess.run(
                ['docker', 'image', 'inspect', image],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False
    
    def run_checks(self,
                   context: CheckContext,
                   config: PrecheckConfig) -> PrecheckSummary:
        """Run selected checks.
        
        Args:
            context: Check execution context
            config: Precheck configuration
            
        Returns:
            Summary of all check results
        """
        # Auto-discover checks if not already done
        self.registry.auto_discover()
        
        # Ensure output directories exist
        context.ensure_dirs()
        
        # Get checks to run
        all_checks = self.registry.get_checks(
            pdk=context.pdk,
            project_type=context.project_type,
        )
        
        # Filter based on config
        checks_to_run = []
        for check_class in all_checks:
            if config.should_run_check(
                check_class.name,
                check_class.category,
                check_class.requires_docker
            ):
                checks_to_run.append(check_class)
        
        # Separate native and Docker checks
        native_checks = [c for c in checks_to_run if not c.requires_docker]
        docker_checks = [c for c in checks_to_run if c.requires_docker]
        
        # Start logging
        if self.logger:
            self.logger.start_precheck(
                str(context.project_path),
                context.pdk,
                context.project_type
            )
            # Pass check names so logger can show pending checks
            check_names = [c.display_name for c in checks_to_run]
            self.logger.log_check_list(len(checks_to_run), check_names)
        
        results = []
        
        # Run native checks first (they're fast)
        for check_class in native_checks:
            if config.dry_run:
                result = CheckResult(
                    check_name=check_class.display_name,
                    status=CheckStatus.SKIPPED,
                    message="Dry run - would execute",
                )
            else:
                result = self._run_native_check(check_class, context)
            
            results.append(result)
            if self.logger:
                self.logger.end_check(result)
        
        # Run Docker checks
        if docker_checks:
            # Check if Docker daemon is running before attempting any Docker checks
            docker_available = self._check_docker_running()
            
            if config.dry_run:
                for check_class in docker_checks:
                    result = CheckResult(
                        check_name=check_class.display_name,
                        status=CheckStatus.SKIPPED,
                        message="Dry run - would execute in Docker",
                    )
                    results.append(result)
                    if self.logger:
                        self.logger.end_check(result)
            elif not docker_available:
                # Docker not running - skip all Docker checks with one message
                if self.logger:
                    self.logger.log_warning("Docker daemon is not running. Skipping all Docker checks.")
                    self.logger.log_warning("Start Docker Desktop and try again.")
                for check_class in docker_checks:
                    result = CheckResult(
                        check_name=check_class.display_name,
                        status=CheckStatus.SKIPPED,
                        message="Docker daemon not running",
                    )
                    results.append(result)
                    if self.logger:
                        self.logger.end_check(result)
            elif not self._check_docker_image_exists(config.docker_image):
                # Docker image not found - provide helpful message
                if self.logger:
                    self.logger.log_warning(f"Docker image '{config.docker_image}' not found locally.")
                    self.logger.log_warning(f"Run: docker pull {config.docker_image}")
                for check_class in docker_checks:
                    result = CheckResult(
                        check_name=check_class.display_name,
                        status=CheckStatus.SKIPPED,
                        message=f"Docker image not found. Run: docker pull {config.docker_image}",
                    )
                    results.append(result)
                    if self.logger:
                        self.logger.end_check(result)
            else:
                # _run_docker_checks handles start_check/end_check internally
                docker_results = self._run_docker_checks(
                    docker_checks, 
                    context, 
                    config.docker_image
                )
                results.extend(docker_results)
        
        # Log skipped checks
        skipped_checks = [c for c in all_checks if c not in checks_to_run]
        for check_class in skipped_checks:
            reason = self._get_skip_reason(check_class, config)
            result = CheckResult(
                check_name=check_class.display_name,
                status=CheckStatus.SKIPPED,
                message=reason,
            )
            results.append(result)
        
        # End logging and get summary
        if self.logger:
            summary = self.logger.end_precheck()
        else:
            summary = PrecheckSummary(
                project_path=str(context.project_path),
                pdk=context.pdk,
                project_type=context.project_type,
                results=results,
            )
        
        return summary
    
    def _run_native_check(self, 
                          check_class: type,
                          context: CheckContext) -> CheckResult:
        """Run a native check directly.
        
        Args:
            check_class: The check class to instantiate and run
            context: Check execution context
            
        Returns:
            The check result
        """
        start_time = time.time()
        started_at = datetime.now()
        
        if self.logger:
            self.logger.start_check(
                check_class.name,
                check_class.display_name,
                requires_docker=False
            )
        
        try:
            check = check_class()
            result = check.run(context)
            
            # Ensure we have proper timing
            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms
            result.started_at = started_at
            result.finished_at = datetime.now()
            result.check_name = check_class.display_name
            
            return result
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_name=check_class.display_name,
                status=CheckStatus.ERROR,
                message=str(e),
                duration_ms=duration_ms,
                started_at=started_at,
                finished_at=datetime.now(),
            )
    
    def _run_docker_checks(self,
                           check_classes: List[type],
                           context: CheckContext,
                           docker_image: str) -> List[CheckResult]:
        """Run Docker checks in a container.
        
        Args:
            check_classes: List of check classes to run
            context: Check execution context
            docker_image: Docker image to use
            
        Returns:
            List of check results
        """
        results = []
        
        # Check if Docker is available
        if not shutil.which('docker'):
            for check_class in check_classes:
                results.append(CheckResult(
                    check_name=check_class.display_name,
                    status=CheckStatus.ERROR,
                    message="Docker not found",
                ))
            return results
        
        # Run each Docker check individually with status updates
        for check_class in check_classes:
            start_time = time.time()
            started_at = datetime.now()
            
            if self.logger:
                self.logger.start_check(
                    check_class.name,
                    check_class.display_name,
                    requires_docker=True
                )
            
            try:
                check = check_class()
                result = check.run(context)
                
                duration_ms = int((time.time() - start_time) * 1000)
                result.duration_ms = duration_ms
                result.started_at = started_at
                result.finished_at = datetime.now()
                result.check_name = check_class.display_name
                
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                result = CheckResult(
                    check_name=check_class.display_name,
                    status=CheckStatus.ERROR,
                    message=str(e),
                    duration_ms=duration_ms,
                    started_at=started_at,
                    finished_at=datetime.now(),
                )
            
            # Update logger immediately after each check completes
            results.append(result)
            if self.logger:
                self.logger.end_check(result)
        
        return results
    
    def _get_skip_reason(self, 
                         check_class: type,
                         config: PrecheckConfig) -> str:
        """Get the reason a check was skipped.
        
        Args:
            check_class: The check class
            config: Precheck configuration
            
        Returns:
            Human-readable skip reason
        """
        if config.native_only and check_class.requires_docker:
            return "Skipped (--native-only)"
        
        if config.skip_checks and check_class.name in config.skip_checks:
            return f"Skipped (--skip {check_class.name})"
        
        if config.checks and check_class.name not in config.checks:
            return "Not in selected checks"
        
        if config.categories and check_class.category not in config.categories:
            return f"Category '{check_class.category}' not selected"
        
        return "Skipped"
