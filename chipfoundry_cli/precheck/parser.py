# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""
Parser for mpw_precheck output.

Parses the log output from mpw_precheck to extract check status,
progress, and results for display in the UI.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Callable


class CheckStatus(Enum):
    """Status of a precheck check."""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    """Result of a single check."""
    name: str
    display_name: str
    status: CheckStatus = CheckStatus.PENDING
    message: Optional[str] = None


@dataclass
class PrecheckProgress:
    """Current progress of precheck execution."""
    total_checks: int = 0
    current_check: int = 0
    current_check_name: str = ""
    checks: Dict[str, CheckResult] = field(default_factory=dict)
    started: bool = False
    finished: bool = False
    success: bool = False
    failed_checks: List[str] = field(default_factory=list)


class PrecheckOutputParser:
    """Parses mpw_precheck output and tracks progress."""
    
    # Regex patterns for parsing mpw_precheck output
    PATTERNS = {
        'start': re.compile(r'\{\{START\}\}'),
        'finish': re.compile(r'\{\{FINISH\}\}'),
        'success': re.compile(r'\{SUCCESS\}'),
        'failure': re.compile(r'\{\{FAILURE\}\}.*?(\d+) Check\(s\) Failed: \[(.*?)\]'),
        'sequence': re.compile(r'\{\{PRECHECK SEQUENCE\}\}.*?\[(.*?)\]'),
        'step_update': re.compile(r'\{\{STEP UPDATE\}\} Executing Check (\d+) of (\d+): (.+)'),
        'check_passed': re.compile(r'\{\{(\w[\w\s\-]+) CHECK PASSED\}\}(.*)'),
        'check_failed': re.compile(r'\{\{(\w[\w\s\-]+) CHECK FAILED\}\}(.*)'),
        'project_type': re.compile(r'\{\{Project Type Info\}\}\s*(\w+)'),
        'project_gds': re.compile(r'\{\{Project GDS Info\}\}\s*(\S+):\s*(\S+)'),
        'tools_info': re.compile(r'\{\{Tools Info\}\}\s*(.+)'),
        'pdks_info': re.compile(r'\{\{PDKs Info\}\}\s*(.+)'),
    }
    
    def __init__(self, 
                 on_check_start: Optional[Callable[[str, int, int], None]] = None,
                 on_check_end: Optional[Callable[[str, CheckStatus, str], None]] = None,
                 on_progress: Optional[Callable[[PrecheckProgress], None]] = None):
        """Initialize the parser.
        
        Args:
            on_check_start: Callback(check_name, current, total) when check starts
            on_check_end: Callback(check_name, status, message) when check ends
            on_progress: Callback(progress) on any progress update
        """
        self.on_check_start = on_check_start
        self.on_check_end = on_check_end
        self.on_progress = on_progress
        
        self.progress = PrecheckProgress()
        self._check_order: List[str] = []
        self._current_check: Optional[str] = None
    
    def parse_line(self, line: str):
        """Parse a single line of mpw_precheck output.
        
        Args:
            line: A line from mpw_precheck stdout/stderr
        """
        # Check for start
        if self.PATTERNS['start'].search(line):
            self.progress.started = True
            self._notify_progress()
            return
        
        # Check for finish
        if self.PATTERNS['finish'].search(line):
            self.progress.finished = True
            self._notify_progress()
            return
        
        # Check for success
        if self.PATTERNS['success'].search(line):
            self.progress.success = True
            self.progress.finished = True
            self._notify_progress()
            return
        
        # Check for failure
        failure_match = self.PATTERNS['failure'].search(line)
        if failure_match:
            count = int(failure_match.group(1))
            failed_list = failure_match.group(2)
            self.progress.failed_checks = [
                c.strip().strip("'") for c in failed_list.split(',')
            ]
            self.progress.success = False
            self.progress.finished = True
            self._notify_progress()
            return
        
        # Check for sequence
        seq_match = self.PATTERNS['sequence'].search(line)
        if seq_match:
            checks_str = seq_match.group(1)
            check_names = [c.strip() for c in checks_str.split(',')]
            self._check_order = check_names
            self.progress.total_checks = len(check_names)
            
            # Initialize check results
            for name in check_names:
                self.progress.checks[name] = CheckResult(
                    name=name.lower().replace(' ', '_').replace('-', '_'),
                    display_name=name,
                    status=CheckStatus.PENDING
                )
            
            self._notify_progress()
            return
        
        # Check for step update (check starting)
        step_match = self.PATTERNS['step_update'].search(line)
        if step_match:
            current = int(step_match.group(1))
            total = int(step_match.group(2))
            check_name = step_match.group(3).strip()
            
            # Mark previous check as completed if still running
            if self._current_check and self._current_check in self.progress.checks:
                prev_check = self.progress.checks[self._current_check]
                if prev_check.status == CheckStatus.RUNNING:
                    prev_check.status = CheckStatus.PASSED
                    if self.on_check_end:
                        self.on_check_end(self._current_check, CheckStatus.PASSED, "")
            
            self._current_check = check_name
            self.progress.current_check = current
            self.progress.current_check_name = check_name
            
            if check_name in self.progress.checks:
                self.progress.checks[check_name].status = CheckStatus.RUNNING
            
            if self.on_check_start:
                self.on_check_start(check_name, current, total)
            
            self._notify_progress()
            return
        
        # Check for check passed
        passed_match = self.PATTERNS['check_passed'].search(line)
        if passed_match:
            check_name = passed_match.group(1).strip()
            message = passed_match.group(2).strip() if passed_match.group(2) else ""
            
            # Find matching check
            matched_check = self._find_check(check_name)
            if matched_check:
                self.progress.checks[matched_check].status = CheckStatus.PASSED
                self.progress.checks[matched_check].message = message
                
                if self.on_check_end:
                    self.on_check_end(matched_check, CheckStatus.PASSED, message)
            
            self._notify_progress()
            return
        
        # Check for check failed
        failed_match = self.PATTERNS['check_failed'].search(line)
        if failed_match:
            check_name = failed_match.group(1).strip()
            message = failed_match.group(2).strip() if failed_match.group(2) else ""
            
            # Find matching check
            matched_check = self._find_check(check_name)
            if matched_check:
                self.progress.checks[matched_check].status = CheckStatus.FAILED
                self.progress.checks[matched_check].message = message
                
                if self.on_check_end:
                    self.on_check_end(matched_check, CheckStatus.FAILED, message)
            
            self._notify_progress()
            return
    
    def _find_check(self, check_name: str) -> Optional[str]:
        """Find a check by name (handles variations in naming)."""
        # Direct match
        if check_name in self.progress.checks:
            return check_name
        
        # Normalize and search
        normalized = check_name.upper()
        for name, check in self.progress.checks.items():
            if name.upper() == normalized:
                return name
            if check.display_name.upper() == normalized:
                return name
        
        # Partial match
        for name, check in self.progress.checks.items():
            if normalized in name.upper() or normalized in check.display_name.upper():
                return name
        
        return None
    
    def _notify_progress(self):
        """Notify progress callback."""
        if self.on_progress:
            self.on_progress(self.progress)
    
    def get_summary(self) -> Dict[str, any]:
        """Get a summary of the precheck results."""
        passed = sum(1 for c in self.progress.checks.values() 
                    if c.status == CheckStatus.PASSED)
        failed = sum(1 for c in self.progress.checks.values() 
                    if c.status == CheckStatus.FAILED)
        pending = sum(1 for c in self.progress.checks.values() 
                     if c.status == CheckStatus.PENDING)
        
        return {
            'total': self.progress.total_checks,
            'passed': passed,
            'failed': failed,
            'pending': pending,
            'success': self.progress.success,
            'finished': self.progress.finished,
            'failed_checks': self.progress.failed_checks,
        }
