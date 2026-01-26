# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Modern precheck logging with animated Rich UI."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich.style import Style
from rich.box import ROUNDED

from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus, PrecheckSummary


# Animated spinner frames for running checks
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Status configuration: (symbol, color, style)
STATUS_CONFIG = {
    CheckStatus.PENDING: ("○", "dim", "dim"),
    CheckStatus.RUNNING: ("●", "cyan", "cyan bold"),
    CheckStatus.PASSED: ("✓", "green", "green"),
    CheckStatus.FAILED: ("✗", "red", "red bold"),
    CheckStatus.SKIPPED: ("○", "yellow", "yellow"),
    CheckStatus.ERROR: ("!", "red", "red bold"),
}


class PrecheckLogger:
    """Modern precheck logger with animated live display.
    
    Features:
    - Live updating table showing all checks in real-time
    - Animated spinners for running checks
    - Color-coded status indicators
    - Structured JSON output for CI/automation
    - Beautiful summary with progress visualization
    """
    
    def __init__(self,
                 console: Console,
                 log_path: Optional[Path] = None,
                 json_output: bool = False,
                 verbose: bool = False):
        """Initialize the logger.
        
        Args:
            console: Rich Console instance for output
            log_path: Optional path to write JSON log file
            json_output: If True, output JSON to stdout instead of rich UI
            verbose: If True, show additional details
        """
        self.console = console
        self.log_path = log_path
        self.json_output = json_output
        self.verbose = verbose
        
        # Internal state
        self._results: List[CheckResult] = []
        self._check_states: Dict[str, Dict[str, Any]] = {}
        self._check_order: List[str] = []
        self._current_check: Optional[str] = None
        self._start_time: Optional[float] = None
        self._summary: Optional[PrecheckSummary] = None
        self._total_checks: int = 0
        self._completed_checks: int = 0
        
        # Live display components
        self._live: Optional[Live] = None
        self._progress: Optional[Progress] = None
        self._main_task = None
        self._spinner_frame: int = 0
        self._last_update: float = 0
    
    def _get_spinner(self) -> str:
        """Get the current spinner frame."""
        return SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
    
    def _advance_spinner(self):
        """Advance the spinner animation."""
        self._spinner_frame += 1
    
    def _create_header_panel(self) -> Panel:
        """Create the header panel with project info."""
        if not self._summary:
            return Panel("", border_style="cyan")
        
        header_text = Text()
        header_text.append("MPW Precheck\n", style="bold cyan")
        header_text.append("Project: ", style="dim")
        header_text.append(f"{self._summary.project_path}\n", style="yellow")
        header_text.append("PDK: ", style="dim")
        header_text.append(f"{self._summary.pdk}", style="yellow")
        header_text.append("  •  ", style="dim")
        header_text.append("Type: ", style="dim")
        header_text.append(f"{self._summary.project_type}", style="yellow")
        
        return Panel(header_text, border_style="cyan", box=ROUNDED)
    
    def _get_visible_window(self) -> tuple:
        """Calculate the visible window of checks to display.
        
        Returns:
            Tuple of (start_index, end_index, hidden_above, hidden_below)
        """
        total_checks = len(self._check_order)
        
        # Get terminal height and calculate max visible rows
        # Reserve space for: header (4 lines) + spacing + errors + summary
        try:
            terminal_height = self.console.size.height
            max_visible = max(5, terminal_height - 12)  # At least 5 checks visible
        except Exception:
            max_visible = 15  # Default fallback
        
        # If all checks fit, show them all
        if total_checks <= max_visible:
            return 0, total_checks, 0, 0
        
        # Find the currently running check index
        running_index = -1
        for i, check_name in enumerate(self._check_order):
            state = self._check_states.get(check_name, {})
            if state.get('status') == CheckStatus.RUNNING:
                running_index = i
                break
        
        # If no running check, show window around the last completed check
        if running_index == -1:
            # Find the last non-pending check
            for i in range(total_checks - 1, -1, -1):
                state = self._check_states.get(self._check_order[i], {})
                if state.get('status') != CheckStatus.PENDING:
                    running_index = i
                    break
            if running_index == -1:
                running_index = 0
        
        # Calculate window centered on running check
        # Keep running check in the middle-upper portion of the window
        context_above = max_visible // 3
        context_below = max_visible - context_above - 1
        
        start = max(0, running_index - context_above)
        end = min(total_checks, start + max_visible)
        
        # Adjust start if we're near the end
        if end == total_checks:
            start = max(0, end - max_visible)
        
        hidden_above = start
        hidden_below = total_checks - end
        
        return start, end, hidden_above, hidden_below
    
    def _create_checks_table(self) -> Group:
        """Create the live checks status table with scrolling."""
        # Get visible window
        start, end, hidden_above, hidden_below = self._get_visible_window()
        
        components = []
        
        # Show "↑ N more" indicator if there are hidden checks above
        if hidden_above > 0:
            above_text = Text()
            above_text.append(f"     ↑ {hidden_above} more above", style="dim cyan")
            components.append(above_text)
        
        # Create the table
        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
            collapse_padding=True,
            expand=True,
        )
        
        table.add_column("Status", width=3, no_wrap=True)
        table.add_column("Name", ratio=1)
        table.add_column("Info", width=35, no_wrap=True, justify="right")
        
        # Only show checks in the visible window
        visible_checks = self._check_order[start:end]
        
        for check_name in visible_checks:
            state = self._check_states.get(check_name, {})
            status = state.get('status', CheckStatus.PENDING)
            display_name = state.get('display_name', check_name)
            duration_ms = state.get('duration_ms')
            message = state.get('message', '')
            
            symbol, color, style = STATUS_CONFIG[status]
            
            # Animate running checks
            if status == CheckStatus.RUNNING:
                symbol = self._get_spinner()
            
            # Format status column
            status_text = Text(symbol, style=style)
            
            # Format name column
            name_text = Text(display_name)
            if status == CheckStatus.RUNNING:
                name_text.stylize("cyan")
            elif status == CheckStatus.PASSED:
                name_text.stylize("green")
            elif status in (CheckStatus.FAILED, CheckStatus.ERROR):
                name_text.stylize("red")
            elif status == CheckStatus.SKIPPED:
                name_text.stylize("dim yellow")
            else:
                name_text.stylize("dim")
            
            # Format info column
            info_text = Text()
            if status == CheckStatus.RUNNING:
                info_text.append("running...", style="cyan")
            elif status == CheckStatus.PASSED:
                if duration_ms:
                    info_text.append(f"passed ", style="green")
                    info_text.append(self._format_duration(duration_ms), style="dim")
                else:
                    info_text.append("passed", style="green")
            elif status == CheckStatus.FAILED:
                info_text.append("failed", style="red bold")
                if duration_ms:
                    info_text.append(f" {self._format_duration(duration_ms)}", style="dim")
            elif status == CheckStatus.ERROR:
                info_text.append("error", style="red bold")
                if duration_ms:
                    info_text.append(f" {self._format_duration(duration_ms)}", style="dim")
            elif status == CheckStatus.SKIPPED:
                skip_msg = message[:30] + "..." if len(message) > 30 else message
                info_text.append(f"skipped", style="yellow")
                if skip_msg:
                    info_text.append(f" ({skip_msg})", style="dim")
            else:
                info_text.append("pending", style="dim")
            
            table.add_row(status_text, name_text, info_text)
        
        components.append(table)
        
        # Show "↓ N more" indicator if there are hidden checks below
        if hidden_below > 0:
            below_text = Text()
            below_text.append(f"     ↓ {hidden_below} more below", style="dim cyan")
            components.append(below_text)
        
        return Group(*components)
    
    def _create_progress_bar(self) -> Progress:
        """Create the main progress bar."""
        return Progress(
            SpinnerColumn(spinner_name="dots"),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=30, style="cyan", complete_style="green", finished_style="green"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        )
    
    def _create_live_display(self) -> Group:
        """Create the full live display layout."""
        self._advance_spinner()
        
        components = []
        
        # Header panel
        components.append(self._create_header_panel())
        components.append(Text())  # Spacing
        
        # Checks table
        components.append(self._create_checks_table())
        
        # Add error details for failed checks
        error_lines = self._get_error_summary()
        if error_lines:
            components.append(Text())
            for line in error_lines:
                components.append(line)
        
        return Group(*components)
    
    def _get_error_summary(self) -> List[Text]:
        """Get condensed error information for failed checks."""
        lines = []
        
        for result in self._results:
            if result.status in (CheckStatus.FAILED, CheckStatus.ERROR):
                # Add separator before first error
                if not lines:
                    lines.append(Text("─" * 50, style="dim red"))
                
                error_text = Text()
                error_text.append("  → ", style="red")
                error_text.append(f"{result.check_name}: ", style="red bold")
                
                # Get a short error message
                msg = result.message or "Check failed"
                if ':' in msg and len(msg) > 60:
                    msg = msg.split(':')[0]
                if len(msg) > 55:
                    msg = msg[:52] + "..."
                error_text.append(msg, style="dim")
                
                lines.append(error_text)
                
                # Show details if available (limit to 2 items)
                if result.details:
                    detail_items = self._extract_detail_items(result.details)
                    for item in detail_items[:2]:
                        detail_text = Text()
                        detail_text.append("      ", style="dim")
                        item_str = str(item)
                        if len(item_str) > 50:
                            item_str = item_str[:47] + "..."
                        detail_text.append(item_str, style="dim")
                        lines.append(detail_text)
                    
                    if len(detail_items) > 2:
                        more_text = Text()
                        more_text.append(f"      ... and {len(detail_items) - 2} more", style="dim")
                        lines.append(more_text)
                
                # Show log path if available
                if result.log_path:
                    log_text = Text()
                    log_text.append("      Log: ", style="dim")
                    log_text.append(str(result.log_path), style="cyan dim")
                    lines.append(log_text)
        
        return lines
    
    def _extract_detail_items(self, details: Dict[str, Any]) -> List[str]:
        """Extract items from details dict for display."""
        detail_keys = [
            'invalid', 'invalid_gpios', 'invalid_values',
            'missing', 'missing_gpios',
            'errors', 'violations', 'failed'
        ]
        
        for key in detail_keys:
            if key in details and isinstance(details[key], list):
                return details[key]
        
        return []
    
    def _format_duration(self, duration_ms: int) -> str:
        """Format duration in milliseconds to human readable."""
        if duration_ms < 1000:
            return f"{duration_ms}ms"
        elif duration_ms < 60000:
            return f"{duration_ms / 1000:.1f}s"
        else:
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) / 1000
            return f"{minutes}m{seconds:.0f}s"
    
    def _update_live(self, force: bool = False):
        """Update the live display.
        
        Args:
            force: If True, bypass throttling and update immediately
        """
        if self._live and not self.json_output:
            current_time = time.time()
            # Throttle updates to max ~15 FPS for smooth animation (unless forced)
            if force or current_time - self._last_update >= 0.066:
                self._live.update(self._create_live_display())
                self._last_update = current_time
    
    def start_precheck(self, project_path: str, pdk: str, project_type: str):
        """Log the start of a precheck run."""
        self._summary = PrecheckSummary(
            project_path=project_path,
            pdk=pdk,
            project_type=project_type,
            started_at=datetime.now(),
        )
        self._start_time = time.time()
        
        if not self.json_output:
            self.console.print()
    
    def log_check_list(self, total_checks: int, check_names: Optional[List[str]] = None):
        """Log the list of checks to be run."""
        self._total_checks = total_checks
        self._completed_checks = 0
        
        if check_names:
            for name in check_names:
                self._check_order.append(name)
                self._check_states[name] = {
                    'status': CheckStatus.PENDING,
                    'display_name': name,
                    'duration_ms': None,
                    'message': '',
                }
        
        if not self.json_output:
            # Start live display (transient=True so we can print final state cleanly)
            self._live = Live(
                self._create_live_display(),
                console=self.console,
                refresh_per_second=15,
                transient=True,
            )
            self._live.start()
    
    def start_check(self, check_name: str, display_name: str, requires_docker: bool = False):
        """Log the start of a check."""
        self._current_check = check_name
        
        # Update state
        if display_name in self._check_states:
            self._check_states[display_name]['status'] = CheckStatus.RUNNING
        else:
            self._check_order.append(display_name)
            self._check_states[display_name] = {
                'status': CheckStatus.RUNNING,
                'display_name': display_name,
                'duration_ms': None,
                'message': '',
            }
        
        # Force update to ensure "running" state is always displayed
        self._update_live(force=True)
    
    def end_check(self, result: CheckResult):
        """Log the end of a check."""
        self._results.append(result)
        self._current_check = None
        self._completed_checks += 1
        
        # Update state
        if result.check_name in self._check_states:
            self._check_states[result.check_name]['status'] = result.status
            self._check_states[result.check_name]['duration_ms'] = result.duration_ms
            self._check_states[result.check_name]['message'] = result.message or ''
        
        self._update_live()
    
    def log_section(self, title: str, subtitle: Optional[str] = None):
        """Log a section header."""
        pass  # Sections are implicit in the live display
    
    def end_precheck(self) -> PrecheckSummary:
        """Log the end of a precheck run and return summary."""
        # Stop live display
        self.stop_live()
        
        if self._summary:
            self._summary.results = self._results
            self._summary.finished_at = datetime.now()
        
        total_duration = time.time() - self._start_time if self._start_time else 0
        
        if self.json_output:
            if self._summary:
                self.console.print(json.dumps(self._summary.to_dict(), indent=2))
        else:
            # Print final state (header + completed checks + errors)
            self._print_final_state()
            self._print_summary(total_duration)
        
        # Write JSON log file
        if self.log_path and self._summary:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, 'w') as f:
                json.dump(self._summary.to_dict(), f, indent=2)
        
        return self._summary
    
    def _create_final_checks_table(self) -> Table:
        """Create the final checks table showing ALL checks (no scrolling)."""
        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
            collapse_padding=True,
            expand=True,
        )
        
        table.add_column("Status", width=3, no_wrap=True)
        table.add_column("Name", ratio=1)
        table.add_column("Info", width=35, no_wrap=True, justify="right")
        
        for check_name in self._check_order:
            state = self._check_states.get(check_name, {})
            status = state.get('status', CheckStatus.PENDING)
            display_name = state.get('display_name', check_name)
            duration_ms = state.get('duration_ms')
            message = state.get('message', '')
            
            symbol, color, style = STATUS_CONFIG[status]
            status_text = Text(symbol, style=style)
            
            # Format name column
            name_text = Text(display_name)
            if status == CheckStatus.PASSED:
                name_text.stylize("green")
            elif status in (CheckStatus.FAILED, CheckStatus.ERROR):
                name_text.stylize("red")
            elif status == CheckStatus.SKIPPED:
                name_text.stylize("dim yellow")
            else:
                name_text.stylize("dim")
            
            # Format info column
            info_text = Text()
            if status == CheckStatus.PASSED:
                if duration_ms:
                    info_text.append(f"passed ", style="green")
                    info_text.append(self._format_duration(duration_ms), style="dim")
                else:
                    info_text.append("passed", style="green")
            elif status == CheckStatus.FAILED:
                info_text.append("failed", style="red bold")
                if duration_ms:
                    info_text.append(f" {self._format_duration(duration_ms)}", style="dim")
            elif status == CheckStatus.ERROR:
                info_text.append("error", style="red bold")
                if duration_ms:
                    info_text.append(f" {self._format_duration(duration_ms)}", style="dim")
            elif status == CheckStatus.SKIPPED:
                skip_msg = message[:30] + "..." if len(message) > 30 else message
                info_text.append(f"skipped", style="yellow")
                if skip_msg:
                    info_text.append(f" ({skip_msg})", style="dim")
            else:
                info_text.append("pending", style="dim")
            
            table.add_row(status_text, name_text, info_text)
        
        return table
    
    def _print_final_state(self):
        """Print the final state of all checks (after live display stops)."""
        # Print header panel
        self.console.print(self._create_header_panel())
        self.console.print()
        
        # Print final checks table (show ALL checks)
        self.console.print(self._create_final_checks_table())
        
        # Print error details
        error_lines = self._get_error_summary()
        if error_lines:
            self.console.print()
            for line in error_lines:
                self.console.print(line)
    
    def _print_summary(self, total_duration: float):
        """Print the final summary panel."""
        passed = sum(1 for r in self._results if r.status == CheckStatus.PASSED)
        failed = sum(1 for r in self._results if r.status in (CheckStatus.FAILED, CheckStatus.ERROR))
        skipped = sum(1 for r in self._results if r.status == CheckStatus.SKIPPED)
        total = len(self._results)
        
        # Format duration
        minutes = int(total_duration // 60)
        seconds = int(total_duration % 60)
        duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        
        # Create summary panel
        summary_text = Text()
        
        # Results line with visual bar
        if total > 0:
            bar_width = 30
            passed_width = int((passed / total) * bar_width) if passed else 0
            failed_width = int((failed / total) * bar_width) if failed else 0
            skipped_width = bar_width - passed_width - failed_width
            
            summary_text.append("  ")
            summary_text.append("█" * passed_width, style="green")
            summary_text.append("█" * failed_width, style="red")
            summary_text.append("░" * skipped_width, style="dim")
            summary_text.append("\n\n")
        
        # Stats
        summary_text.append("  Results: ", style="bold")
        if passed:
            summary_text.append(f"{passed} passed", style="green bold")
        if failed:
            if passed:
                summary_text.append("  ")
            summary_text.append(f"{failed} failed", style="red bold")
        if skipped:
            if passed or failed:
                summary_text.append("  ")
            summary_text.append(f"{skipped} skipped", style="yellow")
        
        summary_text.append(f"\n  Duration: ", style="bold")
        summary_text.append(duration_str, style="cyan")
        
        # Log path
        if self.log_path:
            try:
                cwd = Path.cwd()
                if self.log_path.is_relative_to(cwd):
                    display_path = self.log_path.relative_to(cwd)
                else:
                    display_path = self.log_path
            except (ValueError, AttributeError):
                display_path = self.log_path
            summary_text.append(f"\n  Report: ", style="bold")
            summary_text.append(str(display_path), style="cyan")
        
        # Choose border style based on result
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
    
    def stop_live(self):
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None
    
    def log_error(self, message: str, details: Optional[str] = None):
        """Log an error message."""
        if self.json_output:
            error_data = {'error': message}
            if details:
                error_data['details'] = details
            self.console.print(json.dumps(error_data))
        else:
            self.console.print(f"[red]✗[/red] {message}")
            if details:
                self.console.print(f"  [dim]{details}[/dim]")
    
    def log_warning(self, message: str):
        """Log a warning message."""
        if not self.json_output:
            # If live display is running, we need to print outside of it
            if self._live:
                self._live.stop()
                self.console.print(f"[yellow]⚠[/yellow]  {message}")
                self._live.start()
            else:
                self.console.print(f"[yellow]⚠[/yellow]  {message}")
    
    def log_info(self, message: str):
        """Log an info message."""
        if not self.json_output and self.verbose:
            self.console.print(f"[dim]{message}[/dim]")
    
    def get_results(self) -> List[CheckResult]:
        """Get all check results."""
        return self._results.copy()
