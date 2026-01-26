# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""
Rich-based UI for mpw_precheck.

Provides a beautiful, animated terminal UI for displaying
precheck progress and results.
"""

import time
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.text import Text
from rich.box import ROUNDED

from chipfoundry_cli.precheck.parser import PrecheckProgress, CheckStatus, CheckResult


# Spinner frames for running checks
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Status configuration: (symbol, color)
STATUS_CONFIG = {
    CheckStatus.PENDING: ("○", "dim"),
    CheckStatus.RUNNING: ("●", "cyan"),
    CheckStatus.PASSED: ("✓", "green"),
    CheckStatus.FAILED: ("✗", "red"),
    CheckStatus.SKIPPED: ("○", "yellow"),
}


class PrecheckUI:
    """Rich-based UI for displaying precheck progress."""
    
    # Number of lines reserved for header, progress bar, summary, etc.
    RESERVED_LINES = 12
    # Minimum number of visible checks
    MIN_VISIBLE_CHECKS = 5
    
    def __init__(self, 
                 console: Console,
                 project_path: Path,
                 pdk: str,
                 project_type: str = "digital"):
        """Initialize the UI.
        
        Args:
            console: Rich Console instance
            project_path: Path to the project
            pdk: PDK name
            project_type: Project type (digital, analog, etc.)
        """
        self.console = console
        self.project_path = project_path
        self.pdk = pdk
        self.project_type = project_type
        
        self._live: Optional[Live] = None
        self._progress: Optional[PrecheckProgress] = None
        self._spinner_frame: int = 0
        self._start_time: float = 0
        self._check_order: List[str] = []
        self._raw_lines: List[str] = []
        self._verbose: bool = False
        self._scroll_offset: int = 0
    
    def _get_spinner(self) -> str:
        """Get the current spinner frame."""
        self._spinner_frame += 1
        return SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
    
    def _create_header(self) -> Panel:
        """Create the header panel."""
        header_text = Text()
        header_text.append("MPW Precheck\n", style="bold cyan")
        header_text.append("Project: ", style="dim")
        header_text.append(f"{self.project_path}\n", style="yellow")
        header_text.append("PDK: ", style="dim")
        header_text.append(f"{self.pdk}", style="yellow")
        header_text.append("  •  ", style="dim")
        header_text.append("Type: ", style="dim")
        header_text.append(f"{self.project_type}", style="yellow")
        
        return Panel(header_text, border_style="cyan", box=ROUNDED)
    
    def _create_checks_table(self) -> Table:
        """Create the checks status table."""
        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
            collapse_padding=True,
            expand=True,
        )
        
        table.add_column("Status", width=3, no_wrap=True)
        table.add_column("Name", ratio=1)
        table.add_column("Info", width=40, no_wrap=True, justify="right")
        
        if not self._progress or not self._progress.checks:
            return table
        
        # Get checks in order
        check_names = self._check_order if self._check_order else list(self._progress.checks.keys())
        
        for check_name in check_names:
            if check_name not in self._progress.checks:
                continue
            
            check = self._progress.checks[check_name]
            status = check.status
            symbol, color = STATUS_CONFIG[status]
            
            # Animate running checks
            if status == CheckStatus.RUNNING:
                symbol = self._get_spinner()
            
            # Status symbol
            status_text = Text(symbol, style=color)
            
            # Check name
            name_text = Text(check.display_name)
            if status == CheckStatus.RUNNING:
                name_text.stylize("cyan bold")
            elif status == CheckStatus.PASSED:
                name_text.stylize("green")
            elif status == CheckStatus.FAILED:
                name_text.stylize("red")
            else:
                name_text.stylize("dim")
            
            # Info column
            info_text = Text()
            if status == CheckStatus.RUNNING:
                info_text.append("running...", style="cyan")
            elif status == CheckStatus.PASSED:
                info_text.append("passed", style="green")
            elif status == CheckStatus.FAILED:
                info_text.append("failed", style="red bold")
            elif status == CheckStatus.SKIPPED:
                info_text.append("skipped", style="yellow")
            else:
                info_text.append("pending", style="dim")
            
            table.add_row(status_text, name_text, info_text)
        
        return table
    
    def _create_progress_bar(self) -> Text:
        """Create a simple progress indicator."""
        if not self._progress:
            return Text()
        
        current = self._progress.current_check
        total = self._progress.total_checks
        
        if total == 0:
            return Text()
        
        elapsed = time.time() - self._start_time if self._start_time else 0
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        
        progress_text = Text()
        progress_text.append(f"  Progress: {current}/{total}", style="dim")
        progress_text.append(f"  •  Elapsed: {time_str}", style="dim")
        
        return progress_text
    
    def _create_live_display(self) -> Group:
        """Create the full live display."""
        components = []
        
        # Header
        components.append(self._create_header())
        components.append(Text())
        
        # Progress
        progress_text = self._create_progress_bar()
        if progress_text:
            components.append(progress_text)
            components.append(Text())
        
        # Checks table
        components.append(self._create_checks_table())
        
        return Group(*components)
    
    def start(self, verbose: bool = False):
        """Start the live display."""
        self._verbose = verbose
        self._start_time = time.time()
        
        if not self._verbose:
            self._live = Live(
                self._create_live_display(),
                console=self.console,
                refresh_per_second=10,
                transient=True,
            )
            self._live.start()
        else:
            # In verbose mode, just print the header
            self.console.print(self._create_header())
            self.console.print()
    
    def stop(self):
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None
    
    def update(self, progress: PrecheckProgress):
        """Update the display with new progress."""
        self._progress = progress
        
        # Capture check order on first update with checks
        if progress.checks and not self._check_order:
            self._check_order = list(progress.checks.keys())
        
        if self._live:
            self._live.update(self._create_live_display())
    
    def on_line(self, line: str):
        """Handle a line of output from mpw_precheck."""
        self._raw_lines.append(line)
        
        if self._verbose:
            # In verbose mode, print all lines
            self.console.print(f"[dim]{line}[/dim]")
    
    def print_final_results(self, output_dir: Path):
        """Print the final results after precheck completes."""
        if not self._progress:
            return
        
        # Print header
        self.console.print()
        self.console.print(self._create_header())
        self.console.print()
        
        # Print all checks with final status
        self.console.print(self._create_checks_table())
        
        # Print error details for failed checks
        failed_checks = [
            (name, check) for name, check in self._progress.checks.items()
            if check.status == CheckStatus.FAILED
        ]
        
        if failed_checks:
            self.console.print()
            self.console.print("[red]─[/red]" * 50)
            for name, check in failed_checks:
                error_text = Text()
                error_text.append("  → ", style="red")
                error_text.append(f"{check.display_name}: ", style="red bold")
                msg = check.message or "Check failed"
                if len(msg) > 60:
                    msg = msg[:57] + "..."
                error_text.append(msg, style="dim")
                self.console.print(error_text)
        
        # Print summary
        passed = sum(1 for c in self._progress.checks.values() 
                    if c.status == CheckStatus.PASSED)
        failed = sum(1 for c in self._progress.checks.values() 
                    if c.status == CheckStatus.FAILED)
        
        elapsed = time.time() - self._start_time if self._start_time else 0
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        
        # Create summary panel
        summary_text = Text()
        
        # Visual bar
        total = len(self._progress.checks)
        if total > 0:
            bar_width = 30
            passed_width = int((passed / total) * bar_width)
            failed_width = int((failed / total) * bar_width)
            remaining_width = bar_width - passed_width - failed_width
            
            summary_text.append("  ")
            summary_text.append("█" * passed_width, style="green")
            summary_text.append("█" * failed_width, style="red")
            summary_text.append("░" * remaining_width, style="dim")
            summary_text.append("\n\n")
        
        # Stats
        summary_text.append("  Results: ", style="bold")
        if passed:
            summary_text.append(f"{passed} passed", style="green bold")
        if failed:
            if passed:
                summary_text.append("  ")
            summary_text.append(f"{failed} failed", style="red bold")
        
        summary_text.append(f"\n  Duration: ", style="bold")
        summary_text.append(time_str, style="cyan")
        
        # Log path
        log_path = output_dir / 'logs' / 'precheck.log'
        if log_path.exists():
            try:
                cwd = Path.cwd()
                display_path = log_path.relative_to(cwd)
            except ValueError:
                display_path = log_path
            summary_text.append(f"\n  Log: ", style="bold")
            summary_text.append(str(display_path), style="cyan")
        
        # Choose panel style
        if failed > 0:
            border_style = "red"
            title = "✗ Precheck Failed"
            title_style = "red bold"
        else:
            border_style = "green"
            title = "✓ Precheck Passed"
            title_style = "green bold"
        
        self.console.print()
        self.console.print(Panel(
            summary_text,
            title=f"[{title_style}]{title}[/{title_style}]",
            border_style=border_style,
            box=ROUNDED,
            padding=(1, 2),
        ))
        self.console.print()
