# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Check result types and status definitions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime


class CheckStatus(Enum):
    """Status of a precheck check."""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class CheckResult:
    """Result of a single check execution."""
    check_name: str
    status: CheckStatus
    message: str
    details: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    log_path: Optional[str] = None
    error_snippet: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'check_name': self.check_name,
            'status': self.status.value,
            'message': self.message,
            'details': self.details,
            'duration_ms': self.duration_ms,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'log_path': self.log_path,
        }
    
    @property
    def passed(self) -> bool:
        """Check if the result indicates success."""
        return self.status == CheckStatus.PASSED
    
    @property
    def failed(self) -> bool:
        """Check if the result indicates failure."""
        return self.status in (CheckStatus.FAILED, CheckStatus.ERROR)


@dataclass
class PrecheckSummary:
    """Summary of all precheck results."""
    project_path: str
    pdk: str
    project_type: str
    results: List[CheckResult] = field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    
    @property
    def total_checks(self) -> int:
        """Total number of checks run."""
        return len(self.results)
    
    @property
    def passed_count(self) -> int:
        """Number of passed checks."""
        return sum(1 for r in self.results if r.status == CheckStatus.PASSED)
    
    @property
    def failed_count(self) -> int:
        """Number of failed checks."""
        return sum(1 for r in self.results if r.status in (CheckStatus.FAILED, CheckStatus.ERROR))
    
    @property
    def skipped_count(self) -> int:
        """Number of skipped checks."""
        return sum(1 for r in self.results if r.status == CheckStatus.SKIPPED)
    
    @property
    def all_passed(self) -> bool:
        """Check if all checks passed."""
        return self.failed_count == 0
    
    @property
    def total_duration_ms(self) -> int:
        """Total duration of all checks in milliseconds."""
        return sum(r.duration_ms or 0 for r in self.results)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'project_path': self.project_path,
            'pdk': self.pdk,
            'project_type': self.project_type,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'total_duration_ms': self.total_duration_ms,
            'summary': {
                'total': self.total_checks,
                'passed': self.passed_count,
                'failed': self.failed_count,
                'skipped': self.skipped_count,
            },
            'results': [r.to_dict() for r in self.results],
        }
