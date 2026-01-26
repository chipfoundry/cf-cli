# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Base check abstract class."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, ClassVar

from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus


class BaseCheck(ABC):
    """Abstract base class for all precheck checks.
    
    Each check must define class-level metadata and implement the run() method.
    
    Class Attributes:
        name: Internal name of the check (e.g., 'gpio_defines')
        display_name: Human-readable name (e.g., 'GPIO Defines Check')
        description: Brief description of what the check does
        category: Check category (e.g., 'gpio', 'drc', 'lvs')
        supported_pdks: List of supported PDKs (e.g., ['sky130A', 'sky130B'])
        supported_types: List of supported project types (e.g., ['digital', 'analog'])
        requires_docker: Whether the check requires Docker to run
    
    Example:
        @register_check
        class MyCheck(BaseCheck):
            name = "my_check"
            display_name = "My Check"
            description = "Validates something important"
            category = "validation"
            supported_pdks = ['sky130A', 'sky130B']
            supported_types = ['digital', 'analog']
            requires_docker = False
            
            def run(self, context: CheckContext) -> CheckResult:
                # Perform the check
                if something_is_valid:
                    return CheckResult(
                        check_name=self.display_name,
                        status=CheckStatus.PASSED,
                        message="Validation passed"
                    )
                else:
                    return CheckResult(
                        check_name=self.display_name,
                        status=CheckStatus.FAILED,
                        message="Validation failed: reason"
                    )
    """
    
    # Class-level metadata (must be defined by subclasses)
    name: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str]
    supported_pdks: ClassVar[List[str]]
    supported_types: ClassVar[List[str]]
    requires_docker: ClassVar[bool] = False
    
    def __init__(self):
        """Initialize the check."""
        pass
    
    @abstractmethod
    def run(self, context: CheckContext) -> CheckResult:
        """Execute the check.
        
        Args:
            context: Check execution context containing project paths,
                    PDK info, and output directories
                    
        Returns:
            CheckResult indicating the check outcome
        """
        pass
    
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate check-specific configuration.
        
        Override this method to validate any check-specific configuration
        options before the check runs.
        
        Args:
            config: Check-specific configuration dictionary
            
        Returns:
            True if configuration is valid, False otherwise
        """
        return True
    
    def get_report_path(self, context: CheckContext) -> Optional[str]:
        """Get the path to the check's detailed report file.
        
        Override this method if the check produces a detailed report file.
        
        Args:
            context: Check execution context
            
        Returns:
            Path to the report file, or None if no report is generated
        """
        return None
    
    def _make_passed_result(self, message: str = "Check passed",
                            details: Optional[Dict[str, Any]] = None) -> CheckResult:
        """Helper to create a passed result.
        
        Args:
            message: Success message
            details: Optional additional details
            
        Returns:
            CheckResult with PASSED status
        """
        return CheckResult(
            check_name=self.display_name,
            status=CheckStatus.PASSED,
            message=message,
            details=details,
        )
    
    def _make_failed_result(self, message: str,
                            details: Optional[Dict[str, Any]] = None,
                            log_path: Optional[str] = None,
                            error_snippet: Optional[str] = None) -> CheckResult:
        """Helper to create a failed result.
        
        Args:
            message: Failure message
            details: Optional additional details
            log_path: Optional path to log file for full error details
            error_snippet: Optional snippet of the actual error
            
        Returns:
            CheckResult with FAILED status
        """
        return CheckResult(
            check_name=self.display_name,
            status=CheckStatus.FAILED,
            message=message,
            details=details,
            log_path=log_path,
            error_snippet=error_snippet,
        )
    
    def _make_error_result(self, message: str,
                           details: Optional[Dict[str, Any]] = None,
                           log_path: Optional[str] = None,
                           error_snippet: Optional[str] = None) -> CheckResult:
        """Helper to create an error result.
        
        Args:
            message: Error message
            details: Optional additional details
            log_path: Optional path to log file for full error details
            error_snippet: Optional snippet of the actual error
            
        Returns:
            CheckResult with ERROR status
        """
        return CheckResult(
            check_name=self.display_name,
            status=CheckStatus.ERROR,
            message=message,
            details=details,
            log_path=log_path,
            error_snippet=error_snippet,
        )
    
    def _make_skipped_result(self, message: str = "Check skipped") -> CheckResult:
        """Helper to create a skipped result.
        
        Args:
            message: Skip reason
            
        Returns:
            CheckResult with SKIPPED status
        """
        return CheckResult(
            check_name=self.display_name,
            status=CheckStatus.SKIPPED,
            message=message,
        )
