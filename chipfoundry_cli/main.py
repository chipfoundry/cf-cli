import click
import getpass
from chipfoundry_cli.utils import (
    collect_project_files, ensure_cf_directory, update_or_create_project_json,
    sftp_connect, upload_with_progress, sftp_ensure_dirs, sftp_download_recursive,
    get_config_path, load_user_config, save_user_config, GDS_TYPE_MAP,
    open_html_in_browser, download_with_progress, update_repo_files,
    fetch_versions_from_upstream, parse_user_defines_v, update_user_defines_v,
    get_gpio_config_from_project_json, save_gpio_config_to_project_json,
    GPIO_MODES, GPIO_MODE_DESCRIPTIONS, GPIO_HEX_TO_MODE
)
import os
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
import importlib.metadata
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TaskProgressColumn
import json
import subprocess
import sys
import shutil
import signal

# Textual imports for GPIO grid UI
from textual.app import App, ComposeResult
from textual.widgets import Button, Static, Footer, Header, Label
from textual.containers import Grid, Horizontal, Vertical, Container, ScrollableContainer
from textual.binding import Binding
from textual.screen import ModalScreen

DEFAULT_SSH_KEY = os.path.expanduser('~/.ssh/chipfoundry-key')
DEFAULT_SFTP_HOST = 'sftp.chipfoundry.io'

console = Console()

def get_git_tag(repo_path):
    """Get the current git tag/branch of a repository."""
    try:
        # Try to get exact tag match
        result = subprocess.run(
            ['git', 'describe', '--tags', '--exact-match'],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        
        # Try to get tag from HEAD (works in detached HEAD state)
        result = subprocess.run(
            ['git', 'describe', '--tags'],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            tag = result.stdout.strip()
            # Remove any commit suffix like -1-g1234567
            if '-' in tag:
                tag = tag.split('-')[0]
            return tag
        
        # If no tags, get branch name
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            # In detached HEAD, this returns "HEAD", so try one more thing
            if branch == "HEAD":
                # Get all tags pointing to current commit
                result = subprocess.run(
                    ['git', 'tag', '--points-at', 'HEAD'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Return the first tag
                    return result.stdout.strip().split('\n')[0]
            return branch
    except Exception:
        pass
    return None

def check_version_installed(component_dir, expected_version):
    """Check if a git component is installed with the correct version."""
    if not Path(component_dir).exists():
        return False, None
    
    # Check if the expected version tag exists on the current commit
    try:
        result = subprocess.run(
            ['git', 'tag', '--points-at', 'HEAD'],
            cwd=component_dir,
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            tags = result.stdout.strip().split('\n')
            # Check if our expected version is in the list of tags
            if expected_version in tags:
                return True, expected_version
            # If not, return the first tag as current version
            return False, tags[0] if tags else None
    except Exception:
        pass
    
    # Fallback to get_git_tag if the above fails
    current_version = get_git_tag(component_dir)
    if current_version == expected_version:
        return True, current_version
    return False, current_version

def check_python_package_installed(venv_dir, package_name):
    """Check if a Python package is installed in a venv."""
    if not Path(venv_dir).exists():
        return False
    
    venv_python = Path(venv_dir) / 'bin' / 'python3'
    if not venv_python.exists():
        return False
    
    try:
        result = subprocess.run(
            [str(venv_python), '-m', 'pip', 'show', package_name],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False

def get_project_json_from_cwd():
    cf_path = Path(os.getcwd()) / '.cf' / 'project.json'
    if cf_path.exists():
        with open(cf_path) as f:
            data = json.load(f)
        project_name = data.get('project', {}).get('name')
        return str(Path(os.getcwd())), project_name
    return None, None

def check_project_initialized(project_root_path: Path, command_name: str, dry_run: bool = False, allow_graceful: bool = False):
    """
    Check if project is initialized (has .cf/project.json).
    Raises click.Abort with helpful message if not initialized.
    
    Args:
        project_root_path: Path to project root
        command_name: Name of the command (for error messages)
        dry_run: If True, allows dry-run mode to proceed without initialization
        allow_graceful: If True, returns False instead of raising Abort (for commands that should return 0 on error)
    """
    project_json_path = project_root_path / '.cf' / 'project.json'
    if not project_json_path.exists():
        if allow_graceful:
            return False
        if dry_run:
            # In dry-run mode, allow to proceed but warn
            console.print(f"[yellow]⚠ Project not initialized. Run 'cf init' first for full functionality.[/yellow]")
            return True
        console.print(f"[red]✗ Project not initialized. Please run 'cf init' first.[/red]")
        raise click.Abort()
    return True

@click.group(help="ChipFoundry CLI: Automate project submission and management.")
@click.version_option(importlib.metadata.version("chipfoundry-cli"), "-v", "--version", message="%(version)s")
def main():
    pass

@main.command('config')
def config_cmd():
    """Configure user-level SFTP credentials (username and key)."""
    console.print("[bold cyan]ChipFoundry CLI User Configuration[/bold cyan]")
    username = console.input("Enter your ChipFoundry SFTP username: ").strip()
    key_path = console.input("Enter path to your SFTP private key (leave blank for ~/.ssh/chipfoundry-key): ").strip()
    if not key_path:
        key_path = os.path.expanduser('~/.ssh/chipfoundry-key')
    else:
        key_path = os.path.abspath(os.path.expanduser(key_path))
    config = {
        "sftp_username": username,
        "sftp_key": key_path,
    }
    save_user_config(config)
    console.print(f"[green]Configuration saved to {get_config_path()}[/green]")

@main.command('keygen')
@click.option('--overwrite', is_flag=True, help='Overwrite existing key if it already exists.')
def keygen(overwrite):
    """Generate SSH key for ChipFoundry SFTP access."""
    ssh_dir = Path.home() / '.ssh'
    private_key_path = ssh_dir / 'chipfoundry-key'
    public_key_path = ssh_dir / 'chipfoundry-key.pub'
    
    # Ensure .ssh directory exists
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    
    # Check if key already exists
    if private_key_path.exists() and public_key_path.exists():
        if not overwrite:
            console.print(f"[yellow]SSH key already exists at {private_key_path}[/yellow]")
            console.print("[cyan]Here's your existing public key:[/cyan]")
            with open(public_key_path, 'r') as f:
                public_key = f.read().strip()
                print(f"{public_key}", end="")
            print("")
            console.print("[bold cyan]Next steps:[/bold cyan]")
            console.print("1. Copy the public key above")
            console.print("2. Submit it to the registration form at: https://chipfoundry.io/sftp-registration")
            console.print("3. Wait for account approval")
            console.print("4. Use 'cf config' to configure your SFTP credentials")
            return
        else:
            console.print(f"[yellow]Overwriting existing key at {private_key_path}[/yellow]")
            # Remove existing files
            if private_key_path.exists():
                private_key_path.unlink()
            if public_key_path.exists():
                public_key_path.unlink()
    
    # Generate new SSH key
    console.print("[cyan]Generating new RSA SSH key for ChipFoundry...[/cyan]")
    
    try:
        # Use ssh-keygen to generate the key
        cmd = [
            'ssh-keygen',
            '-t', 'rsa',
            '-b', '4096',
            '-f', str(private_key_path),
            '-N', ''  # No passphrase
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Set proper permissions
        private_key_path.chmod(0o600)
        public_key_path.chmod(0o644)
        
        console.print(f"[green]SSH key generated successfully![/green]")
        console.print(f"[cyan]Private key: {private_key_path}[/cyan]")
        console.print(f"[cyan]Public key: {public_key_path}[/cyan]")
        
        # Read and display the public key
        with open(public_key_path, 'r') as f:
            public_key = f.read().strip()
        
        console.print("[bold cyan]Your public key:[/bold cyan]")
        print(f"{public_key}", end="")
        print("")
        
        # Display instructions
        console.print("[bold cyan]Next steps:[/bold cyan]")
        console.print("1. Copy the public key above")
        console.print("2. Submit it to the registration form at: https://chipfoundry.io/sftp-registration")
        console.print("3. Wait for account approval")
        console.print("4. Use 'cf config' to configure your SFTP credentials")
        
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to generate SSH key: {e}[/red]")
        if e.stderr:
            console.print(f"[red]Error details: {e.stderr}[/red]")
        raise click.Abort()
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise click.Abort()

@main.command('keyview')
def keyview():
    """Display the current ChipFoundry SSH key."""
    ssh_dir = Path.home() / '.ssh'
    private_key_path = ssh_dir / 'chipfoundry-key'
    public_key_path = ssh_dir / 'chipfoundry-key.pub'
    
    if not public_key_path.exists():
        console.print("[red]No ChipFoundry SSH key found.[/red]")
        console.print("[yellow]Run 'cf keygen' to generate a new key.[/yellow]")
        raise click.Abort()
    
    console.print("[cyan]Your ChipFoundry SSH public key:[/cyan]")
    with open(public_key_path, 'r') as f:
        public_key = f.read().strip()
        print(f"{public_key}")
    print("")
    console.print("[bold cyan]Next steps:[/bold cyan]")
    console.print("1. Copy the public key above")
    console.print("2. Submit it to the registration form at: https://chipfoundry.io/sftp-registration")
    console.print("3. Wait for account approval")
    console.print("4. Use 'cf config' to configure your SFTP credentials")

@main.command('init')
@click.option('--project-root', required=False, type=click.Path(file_okay=False), help='Directory to create the project in (defaults to current directory).')
@click.option('--shuttle', default=None, help='Shuttle name or ID to associate with the project.')
@click.option('--description', default=None, help='Project description.')
def init(project_root, shuttle, description):
    """Initialize a new ChipFoundry project (.cf/project.json) in the given directory."""
    if not project_root:
        project_root = os.getcwd()
    cf_dir = Path(project_root) / '.cf'
    cf_dir.mkdir(parents=True, exist_ok=True)
    project_json_path = cf_dir / 'project.json'

    existing_platform_id = None
    if project_json_path.exists():
        with open(project_json_path) as f:
            existing_data = json.load(f)
        existing_platform_id = existing_data.get('project', {}).get('platform_project_id')
        if existing_platform_id:
            console.print(f"[yellow]This project is already linked to platform project {existing_platform_id}.[/yellow]")
            console.print("Use [bold]cf status[/bold] to check it or [bold]cf unlink[/bold] to disconnect.")
            return
        overwrite = console.input(f"[yellow]project.json already exists at {project_json_path}. Overwrite? (y/N): [/yellow]").strip().lower()
        if overwrite != 'y':
            console.print("[red]Aborted project initialization.[/red]")
            return

    config = load_user_config()
    username = config.get("sftp_username")
    if not username:
        console.print("[bold red]No SFTP username found in user config. Please run 'chipfoundry config' first.[/bold red]")
        raise click.Abort()

    gds_dir = Path(project_root) / 'gds'
    gds_type = None
    for gds_name, gtype in GDS_TYPE_MAP.items():
        if (gds_dir / gds_name).exists():
            gds_type = gtype
            break

    default_name = Path(project_root).name
    name = console.input(f"Project name (detected: [cyan]{default_name}[/cyan]): ").strip() or default_name

    if gds_type:
        project_type = console.input(f"Project type (digital/analog/openframe) (detected: [cyan]{gds_type}[/cyan]): ").strip() or gds_type
    else:
        project_type = console.input("Project type (digital/analog/openframe): ").strip()

    version = "1"
    data = {
        "project": {
            "name": name,
            "type": project_type,
            "user": username,
            "version": version,
            "user_project_wrapper_hash": "",
            "submission_state": "Draft"
        }
    }

    api_key = config.get('api_key')
    if api_key:
        shuttle_id = None
        if not shuttle:
            try:
                shuttles = _api_get("/shuttles/available")
                if shuttles:
                    shuttles.sort(key=lambda s: s.get('tapeout_date', '9999-12-31'))
                    console.print("\n[bold]Available shuttles:[/bold]")
                    for i, s in enumerate(shuttles, 1):
                        deadline = s.get('tapeout_date', '')
                        console.print(f"  [cyan]{i}[/cyan]. {s['name']}{f' — submission deadline {deadline}' if deadline else ''}")
                    console.print(f"  [cyan]{len(shuttles) + 1}[/cyan]. Skip — choose later")
                    choice = console.input("\nSelect shuttle: ").strip()
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(shuttles):
                            shuttle_id = shuttles[idx]['id']
                    except (ValueError, IndexError):
                        pass
            except SystemExit:
                console.print("[dim]Could not fetch shuttles — continuing without shuttle selection.[/dim]")
        else:
            shuttle_id = shuttle

        create_data = {
            "name": name,
            "description": description or "",
            "design_type": project_type,
            "registration_source": "cli",
        }
        if shuttle_id:
            create_data["shuttle_id"] = str(shuttle_id)

        try:
            project_resp = _api_post("/projects", create_data)
            platform_id = project_resp.get('id')
            data['project']['platform_project_id'] = platform_id

            with open(project_json_path, 'w') as f:
                json.dump(data, f, indent=2)

            portal_url = _get_portal_url()
            console.print(f"\n[green]✓ Project created on platform[/green]")
            console.print(f"  Name:    {name}")
            console.print(f"  ID:      {platform_id}")
            if project_resp.get('shuttle_name'):
                console.print(f"  Shuttle: {project_resp['shuttle_name']}")
            console.print(f"  Status:  Draft")
            console.print(f"  Portal:  {portal_url}/projects/{platform_id}")
            return
        except SystemExit:
            console.print("[yellow]Platform project creation failed — saving local project only.[/yellow]")
    else:
        console.print("[dim]Tip: Run [bold]cf login[/bold] to connect this project to the platform.[/dim]")

    with open(project_json_path, 'w') as f:
        json.dump(data, f, indent=2)
    console.print(f"[green]✓ Initialized project at {project_json_path}[/green]")

@main.command('gpio-config')
@click.option('--project-root', required=False, type=click.Path(exists=True, file_okay=False), help='Path to the project directory (defaults to current directory).')
@click.option('--view', is_flag=True, help='Display current GPIO configuration summary without editing.')
def gpio_config(project_root, view):
    """Configure GPIO settings interactively and save to project config and user_defines.v."""
    if not project_root:
        project_root = os.getcwd()
    
    project_root = Path(project_root)
    
    project_json_path = project_root / '.cf' / 'project.json'
    
    # Auto-initialize if project.json doesn't exist
    if not project_json_path.exists():
        console.print("[yellow]Project not initialized. Auto-initializing...[/yellow]")
        cf_dir = project_root / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        
        # Get username from user config
        config = load_user_config()
        username = config.get("sftp_username", "unknown")
        
        # Auto-detect project type from GDS file name
        gds_dir = project_root / 'gds'
        gds_type = None
        for gds_name, gtype in GDS_TYPE_MAP.items():
            if (gds_dir / gds_name).exists():
                gds_type = gtype
                break
        
        # Default project name to directory name
        default_name = Path(project_root).name
        
        # Default project type
        project_type = gds_type if gds_type else 'digital'
        
        # Create minimal project.json
        data = {
            "project": {
                "name": default_name,
                "type": project_type,
                "user": username,
                "version": "1",
                "user_project_wrapper_hash": "",
                "submission_state": "Draft"
            }
        }
        with open(project_json_path, 'w') as f:
            json.dump(data, f, indent=2)
        console.print(f"[green]Auto-initialized project at {project_json_path}[/green]")
    
    # Load project type from project.json
    with open(project_json_path, 'r') as f:
        project_data = json.load(f)
    project_type = project_data.get('project', {}).get('type', 'digital')
    
    # For openframe, GPIO config is not needed
    if project_type == 'openframe':
        console.print("[red]GPIO configuration is not available for openframe projects.[/red]")
        console.print("[yellow]Openframe projects do not use user_defines.v.[/yellow]")
        raise click.Abort()
    
    user_defines_path = project_root / 'verilog' / 'rtl' / 'user_defines.v'
    
    # Load existing GPIO configs from project.json or user_defines.v
    existing_configs = get_gpio_config_from_project_json(str(project_json_path))
    if not existing_configs and user_defines_path.exists():
        existing_configs = parse_user_defines_v(str(user_defines_path))
    
    # Determine GPIO range based on project type
    if project_type == 'analog':  # caravan
        available_gpios = list(range(5, 14)) + list(range(25, 38))
        user_to_real_map = {}
        user_num = 5
        for real_gpio in available_gpios:
            user_to_real_map[user_num] = real_gpio
            user_num += 1
        gpio_label = "Caravan"
        gpio_note = "Note: GPIO 14-24 unavailable. Numbers 14-26 map to GPIO 25-37."
        user_gpio_range = list(range(5, 27))
    else:  # digital (caravel)
        available_gpios = list(range(5, 38))
        user_to_real_map = {gpio: gpio for gpio in available_gpios}
        gpio_label = "Caravel"
        gpio_note = None
        user_gpio_range = list(range(5, 38))
    
    total_pins = len(user_to_real_map)
    
    # Mode shortcuts - short names that map to full mode keys
    MODE_SHORTCUTS = {
        "out": "user_output", "output": "user_output", "o": "user_output",
        "in": "user_input_nopull", "input": "user_input_nopull", "i": "user_input_nopull",
        "in-pd": "user_input_pulldown", "input-pd": "user_input_pulldown", "pulldown": "user_input_pulldown",
        "in-pu": "user_input_pullup", "input-pu": "user_input_pullup", "pullup": "user_input_pullup",
        "bidir": "user_bidirectional", "bidirectional": "user_bidirectional", "b": "user_bidirectional",
        "analog": "user_analog", "ana": "user_analog", "a": "user_analog",
        "out-mon": "user_output_monitored", "monitored": "user_output_monitored",
        # Management modes
        "mgmt-out": "mgmt_output", "mgmt-in": "mgmt_input_nopull", 
        "mgmt-bidir": "mgmt_bidirectional", "mgmt-analog": "mgmt_analog",
    }
    
    def resolve_mode(mode_str):
        """Resolve a mode string (shortcut or full name) to the mode key."""
        mode_str = mode_str.lower().strip()
        if mode_str in MODE_SHORTCUTS:
            return MODE_SHORTCUTS[mode_str]
        # Check if it's already a valid mode key
        mode_options = [key for key in GPIO_MODES.keys() if key != "invalid"]
        if mode_str in mode_options:
            return mode_str
        # Partial match
        matches = [m for m in mode_options if m.startswith(mode_str)]
        if len(matches) == 1:
            return matches[0]
        return None
    
    def parse_gpio_range(input_str, valid_gpios):
        """Parse '5-10', '5,7,9', or '5-10,15' into list of GPIO numbers."""
        selected = set()
        parts = input_str.replace(' ', '').split(',')
        for part in parts:
            if '-' in part:
                try:
                    start, end = part.split('-', 1)
                    for g in range(int(start), int(end) + 1):
                        if g in valid_gpios:
                            selected.add(g)
                except ValueError:
                    pass
            else:
                try:
                    g = int(part)
                    if g in valid_gpios:
                        selected.add(g)
                except ValueError:
                    pass
        return sorted(selected)
    
    def format_gpio_ranges(gpio_list):
        """Convert [5,6,7,10,11,15] to '5-7, 10-11, 15'."""
        if not gpio_list:
            return "-"
        gpio_list = sorted(gpio_list)
        ranges = []
        start = gpio_list[0]
        end = start
        for g in gpio_list[1:]:
            if g == end + 1:
                end = g
            else:
                ranges.append(f"{start}-{end}" if start != end else str(start))
                start = end = g
        ranges.append(f"{start}-{end}" if start != end else str(start))
        return ", ".join(ranges)
    
    def find_mode_key(mode_value):
        """Find the mode key for a given mode value."""
        if not mode_value:
            return None
        for key, mode_name in GPIO_MODES.items():
            if mode_name == mode_value and key != "invalid":
                return key
        return None
    
    def display_summary(gpio_configs, user_to_real_map):
        """Display a summary of GPIO configuration grouped by mode."""
        mode_groups = {}
        for user_gpio, real_gpio in user_to_real_map.items():
            mode_value = gpio_configs.get(real_gpio)
            mode_key = find_mode_key(mode_value) if mode_value else None
            if mode_key not in mode_groups:
                mode_groups[mode_key] = []
            mode_groups[mode_key].append(user_gpio)
        
        console.print()
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Mode", style="cyan", width=24)
        table.add_column("Count", justify="right", width=6)
        table.add_column("GPIOs")
        
        # Sort by count (most common first)
        for mode_key in sorted(mode_groups.keys(), key=lambda k: -len(mode_groups[k])):
            gpios = mode_groups[mode_key]
            display_name = mode_key if mode_key else "[red]unconfigured[/red]"
            style = ""
            if mode_key:
                if "output" in mode_key: style = "[green]"
                elif "input" in mode_key: style = "[cyan]"
                elif "bidirectional" in mode_key: style = "[yellow]"
                elif "analog" in mode_key: style = "[magenta]"
                display_name = f"{style}{mode_key}[/]"
            table.add_row(display_name, str(len(gpios)), format_gpio_ranges(gpios))
        
        console.print(table)
    
    # Initialize gpio_configs
    gpio_configs = existing_configs.copy() if existing_configs else {}
    
    # If --view flag is set, just display the summary and return
    if view:
        if not gpio_configs:
            console.print("[yellow]No GPIO configuration found.[/yellow]")
            console.print("[dim]Run 'cf gpio-config' to configure GPIOs.[/dim]")
            return
        console.print(f"\n[bold cyan]GPIO Configuration ({gpio_label})[/bold cyan]")
        display_summary(gpio_configs, user_to_real_map)
        console.print()
        return
    
    # ========================
    # HEADER
    # ========================
    
    
    def get_mode_display(mode_key):
        """Get display name for a mode."""
        names = {
            "user_output": "user output",
            "user_output_monitored": "user output monitored",
            "user_input_nopull": "user input",
            "user_input_pulldown": "user input pulldown",
            "user_input_pullup": "user input pullup",
            "user_bidirectional": "user bidirectional",
            "user_analog": "user analog",
            "mgmt_output": "mgmt output",
            "mgmt_input_nopull": "mgmt input",
            "mgmt_input_pulldown": "mgmt input pulldown",
            "mgmt_input_pullup": "mgmt input pullup",
            "mgmt_bidirectional": "mgmt bidirectional",
            "mgmt_analog": "mgmt analog",
        }
        return names.get(mode_key, "not set")
    
    def get_mode_color(mode_key):
        """Get color for a mode."""
        if not mode_key:
            return "red"
        elif "output" in mode_key:
            return "ansi_bright_green"
        elif "input" in mode_key:
            return "cyan"
        elif "bidirectional" in mode_key:
            return "yellow"
        elif "analog" in mode_key:
            return "magenta"
        return "white"
    
    # ========================
    # STEP 2: Textual Grid UI for GPIO Configuration
    # ========================
    
    # All available modes for the selector
    ALL_MODES = [
        ("user_output", "User Output"),
        ("user_output_monitored", "User Output Monitored"),
        ("user_input_nopull", "User Input (no pull)"),
        ("user_input_pullup", "User Input (pull-up)"),
        ("user_input_pulldown", "User Input (pull-down)"),
        ("user_bidirectional", "User Bidirectional"),
        ("user_analog", "User Analog"),
        ("mgmt_output", "Mgmt Output"),
        ("mgmt_input_nopull", "Mgmt Input (no pull)"),
        ("mgmt_input_pullup", "Mgmt Input (pull-up)"),
        ("mgmt_input_pulldown", "Mgmt Input (pull-down)"),
        ("mgmt_bidirectional", "Mgmt Bidirectional"),
        ("mgmt_analog", "Mgmt Analog"),
    ]
    
    class NoKeyScrollContainer(ScrollableContainer):
        """ScrollableContainer that doesn't capture arrow keys."""
        can_focus = False
        BINDINGS = []
    
    class GPIOButton(Static):
        """A widget representing a single GPIO pin."""
        
        can_focus = True
        
        def __init__(self, gpio_num: int, mode_key: str = None, **kwargs):
            self.gpio_num = gpio_num
            self.mode_key = mode_key
            self.is_selected = False
            # Create initial label - use two lines
            abbrev = get_mode_display(mode_key) if mode_key else "not set"
            super().__init__(f"[b]{gpio_num}[/b]\n{abbrev}", **kwargs)
            self.id = f"gpio_{gpio_num}"
        
        def _update_display(self):
            """Update the display text."""
            abbrev = get_mode_display(self.mode_key) if self.mode_key else "not set"
            self.update(f"[b]{self.gpio_num}[/b]\n{abbrev}")
        
        def _update_style(self):
            """Update widget style based on mode and selection."""
            color = get_mode_color(self.mode_key)
            if self.is_selected:
                self.add_class("selected")
            else:
                self.remove_class("selected")
                self.styles.color = color
                self.styles.border = ("solid", color)
        
        def on_mount(self):
            """Set initial style on mount."""
            self._update_style()
        
        def set_mode(self, mode_key: str):
            """Update the GPIO mode."""
            self.mode_key = mode_key
            self._update_display()
            self._update_style()
        
        def toggle_selected(self):
            """Toggle selection state."""
            self.is_selected = not self.is_selected
            self._update_style()
        
        def deselect(self):
            """Clear selection."""
            self.is_selected = False
            self._update_style()
        
        def on_click(self):
            """Handle click - toggle selection."""
            self.toggle_selected()
            # Update current index to this button
            try:
                self.app.current_index = self.app.gpio_list.index(self.gpio_num)
                self.app._highlight_current()
            except (ValueError, AttributeError):
                pass
            self.app._update_status()
    
    class ModeSelectScreen(ModalScreen):
        """Modal screen for selecting GPIO mode."""
        
        BINDINGS = [
            Binding("escape", "cancel", "Cancel"),
            Binding("up", "move_up", "Up", show=False, priority=True),
            Binding("down", "move_down", "Down", show=False, priority=True),
            Binding("enter", "select_current", "Select", show=False, priority=True),
        ]
        
        CSS = """
        ModeSelectScreen {
            align: center middle;
        }
        
        #mode-dialog {
            width: 60;
            height: auto;
            max-height: 90%;
            padding: 1 2;
            background: $surface;
            border: solid cyan;
            overflow-y: auto;
        }
        
        #mode-title {
            text-align: center;
            text-style: bold;
            margin-bottom: 1;
            color: cyan;
        }
        
        .section-label {
            text-style: bold;
            margin-top: 1;
            color: white;
        }
        
        .mode-btn {
            width: 100%;
            margin: 0;
        }
        
        .mode-btn.current-mode {
            border: double white;
        }
        
        .mode-btn-output {
            color: ansi_bright_green;
        }
        
        .mode-btn-input {
            color: cyan;
        }
        
        .mode-btn-bidir {
            color: yellow;
        }
        
        .mode-btn-analog {
            color: magenta;
        }
        
        #cancel-row {
            margin-top: 1;
            align: center middle;
        }
        """
        
        def __init__(self, gpio_nums: list, **kwargs):
            super().__init__(**kwargs)
            self.gpio_nums = gpio_nums
            self.current_idx = 0
            self.mode_buttons = []
        
        def compose(self) -> ComposeResult:
            gpio_str = ", ".join(str(g) for g in self.gpio_nums[:5])
            if len(self.gpio_nums) > 5:
                gpio_str += f"... ({len(self.gpio_nums)} total)"
            
            with Vertical(id="mode-dialog"):
                yield Label(f"Select mode for GPIO: {gpio_str}", id="mode-title")
                yield Label("[dim]Use ↑↓ arrows and Enter, or click[/dim]")
                
                # User modes section
                yield Label("── User Modes ──", classes="section-label")
                for mode_key, mode_name in ALL_MODES:
                    if mode_key.startswith("user_"):
                        if "output" in mode_key:
                            btn_class = "mode-btn mode-btn-output"
                        elif "input" in mode_key:
                            btn_class = "mode-btn mode-btn-input"
                        elif "bidirectional" in mode_key:
                            btn_class = "mode-btn mode-btn-bidir"
                        elif "analog" in mode_key:
                            btn_class = "mode-btn mode-btn-analog"
                        else:
                            btn_class = "mode-btn"
                        yield Button(mode_name, id=f"mode_{mode_key}", classes=btn_class)
                
                # Management modes section
                yield Label("── Management Modes ──", classes="section-label")
                for mode_key, mode_name in ALL_MODES:
                    if mode_key.startswith("mgmt_"):
                        if "output" in mode_key:
                            btn_class = "mode-btn mode-btn-output"
                        elif "input" in mode_key:
                            btn_class = "mode-btn mode-btn-input"
                        elif "bidirectional" in mode_key:
                            btn_class = "mode-btn mode-btn-bidir"
                        elif "analog" in mode_key:
                            btn_class = "mode-btn mode-btn-analog"
                        else:
                            btn_class = "mode-btn"
                        yield Button(mode_name, id=f"mode_{mode_key}", classes=btn_class)
                
                with Horizontal(id="cancel-row"):
                    yield Button("Cancel", variant="error", id="cancel-btn")
        
        def on_mount(self) -> None:
            """Initialize on mount."""
            # Collect all mode buttons
            self.mode_buttons = list(self.query(".mode-btn"))
            if self.mode_buttons:
                self._highlight_current()
        
        def _highlight_current(self) -> None:
            """Highlight current button."""
            for i, btn in enumerate(self.mode_buttons):
                if i == self.current_idx:
                    btn.add_class("current-mode")
                    btn.focus()
                else:
                    btn.remove_class("current-mode")
        
        def action_move_up(self) -> None:
            """Move selection up."""
            if self.current_idx > 0:
                self.current_idx -= 1
                self._highlight_current()
        
        def action_move_down(self) -> None:
            """Move selection down."""
            if self.current_idx < len(self.mode_buttons) - 1:
                self.current_idx += 1
                self._highlight_current()
        
        def action_select_current(self) -> None:
            """Select the current mode."""
            if 0 <= self.current_idx < len(self.mode_buttons):
                btn = self.mode_buttons[self.current_idx]
                mode_key = btn.id[5:]  # Remove "mode_" prefix
                self.dismiss(mode_key)
        
        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "cancel-btn":
                self.dismiss(None)
            elif event.button.id.startswith("mode_"):
                mode_key = event.button.id[5:]  # Remove "mode_" prefix
                self.dismiss(mode_key)
        
        def action_cancel(self):
            self.dismiss(None)
    
    class GPIOGridApp(App):
        """Textual app for GPIO grid configuration."""
        
        GRID_COLS = 6  # Number of columns in the grid
        
        CSS = """
        Screen {
            align: center middle;
        }
        
        #main-container {
            width: 100%;
            height: 100%;
            padding: 1;
        }
        
        #title {
            text-align: center;
            text-style: bold;
            color: cyan;
            margin-bottom: 1;
        }
        
        #grid-scroll {
            height: 1fr;
            width: 100%;
        }
        
        #gpio-grid {
            grid-size: 6;
            grid-gutter: 0;
            padding: 0;
            height: auto;
            width: 100%;
        }
        
        GPIOButton {
            width: 1fr;
            min-width: 20;
            height: 4;
            text-align: center;
            border: solid grey;
            content-align: center middle;
            padding: 0;
        }
        
        GPIOButton.current {
            border: double cyan;
            background: $primary;
        }
        
        GPIOButton.selected {
            background: $success;
            color: black;
        }
        
        #legend {
            text-align: center;
            color: grey;
            margin-top: 1;
        }
        
        #status {
            text-align: center;
            margin-top: 1;
            color: yellow;
        }
        
        #mode-dialog {
            width: 70%;
            max-width: 80;
            height: auto;
            padding: 2;
            background: $surface;
            border: solid cyan;
        }
        
        #mode-title {
            text-align: center;
            text-style: bold;
            margin-bottom: 1;
        }
        
        #mode-select {
            width: 100%;
            margin-bottom: 1;
        }
        
        #mode-buttons {
            align: center middle;
            margin-top: 1;
        }
        
        #mode-buttons Button {
            margin: 0 1;
        }
        """
        
        BINDINGS = [
            Binding("up", "nav_up", "Up", show=False, priority=True),
            Binding("down", "nav_down", "Down", show=False, priority=True),
            Binding("left", "nav_left", "Left", show=False, priority=True),
            Binding("right", "nav_right", "Right", show=False, priority=True),
            Binding("space", "toggle", "Select", priority=True),
            Binding("enter", "open_mode", "Set Mode", priority=True),
            Binding("a", "select_all", "Select All"),
            Binding("n", "select_none", "Clear"),
            Binding("d", "done", "Save & Exit"),
            Binding("q", "quit", "Quit"),
        ]
        
        def __init__(self, gpio_configs, user_to_real_map, user_gpio_range, gpio_label="", **kwargs):
            super().__init__(**kwargs)
            self.gpio_configs = gpio_configs
            self.user_to_real_map = user_to_real_map
            self.user_gpio_range = user_gpio_range
            self.gpio_label = gpio_label
            self.gpio_buttons = {}
            self.gpio_list = sorted(user_to_real_map.keys())
            self.current_index = 0
        
        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            
            with Vertical(id="main-container"):
                yield Label(f"GPIO Configuration ({self.gpio_label}) - Arrows: navigate, Space: select, Enter: set mode, D: done", id="title")
                
                with NoKeyScrollContainer(id="grid-scroll"):
                    with Grid(id="gpio-grid"):
                        for gpio_num in self.gpio_list:
                            real_gpio = self.user_to_real_map[gpio_num]
                            mode_value = self.gpio_configs.get(real_gpio)
                            mode_key = find_mode_key(mode_value) if mode_value else None
                            btn = GPIOButton(gpio_num, mode_key)
                            self.gpio_buttons[gpio_num] = btn
                            yield btn
                
                yield Label("[Space] toggle  [Enter] set mode  [A] select all  [N] select none  [D] done", id="legend")
                yield Label("", id="status")
            
            yield Footer()
        
        def on_mount(self) -> None:
            """Initialize on mount."""
            if self.gpio_list:
                self.current_index = 0
                self._highlight_current()
                self._update_status()
        
        def _highlight_current(self) -> None:
            """Highlight the GPIO at current_index and scroll into view."""
            # Remove highlight from all
            for btn in self.gpio_buttons.values():
                btn.remove_class("current")
            # Add highlight to current and scroll into view
            if 0 <= self.current_index < len(self.gpio_list):
                gpio_num = self.gpio_list[self.current_index]
                btn = self.gpio_buttons[gpio_num]
                btn.add_class("current")
                btn.scroll_visible()
        
        def _is_modal_active(self) -> bool:
            """Check if a modal screen is currently active."""
            return len(self.screen_stack) > 1
        
        def action_nav_up(self) -> None:
            """Move up one row."""
            if self._is_modal_active():
                self.screen.action_move_up()
                return
            new_idx = self.current_index - self.GRID_COLS
            if new_idx >= 0:
                self.current_index = new_idx
                self._highlight_current()
        
        def action_nav_down(self) -> None:
            """Move down one row."""
            if self._is_modal_active():
                self.screen.action_move_down()
                return
            new_idx = self.current_index + self.GRID_COLS
            if new_idx < len(self.gpio_list):
                self.current_index = new_idx
                self._highlight_current()
        
        def action_nav_left(self) -> None:
            """Move left one column."""
            if self._is_modal_active():
                return  # No left/right in modal
            if self.current_index > 0:
                self.current_index -= 1
                self._highlight_current()
        
        def action_nav_right(self) -> None:
            """Move right one column."""
            if self._is_modal_active():
                return  # No left/right in modal
            if self.current_index < len(self.gpio_list) - 1:
                self.current_index += 1
                self._highlight_current()
        
        def action_toggle(self) -> None:
            """Toggle selection of current GPIO."""
            if self._is_modal_active():
                return
            if 0 <= self.current_index < len(self.gpio_list):
                gpio_num = self.gpio_list[self.current_index]
                self.gpio_buttons[gpio_num].toggle_selected()
                self._update_status()
        
        def action_open_mode(self) -> None:
            """Open mode selector or confirm selection in modal."""
            if self._is_modal_active():
                self.screen.action_select_current()
                return
            self.action_set_mode()
        
        def _get_selected_gpios(self) -> list:
            """Get list of selected GPIO numbers."""
            return [num for num, btn in self.gpio_buttons.items() if btn.is_selected]
        
        def _update_status(self):
            """Update the status label."""
            selected = self._get_selected_gpios()
            status = self.query_one("#status", Label)
            if selected:
                status.update(f"Selected: {', '.join(str(g) for g in sorted(selected))} ({len(selected)} pins)")
            else:
                status.update("No pins selected - Space to select, Enter to set mode for focused pin")
        
        def action_toggle_select(self) -> None:
            """Toggle selection of focused GPIO."""
            focused = self.focused
            if isinstance(focused, GPIOButton):
                focused.toggle_selected()
                self._update_status()
        
        def action_select_all(self) -> None:
            """Select all GPIOs."""
            for btn in self.gpio_buttons.values():
                btn.is_selected = True
                btn._update_style()
            self._update_status()
        
        def action_select_none(self) -> None:
            """Clear all selections."""
            for btn in self.gpio_buttons.values():
                btn.deselect()
            self._update_status()
        
        def action_set_mode(self) -> None:
            """Open mode selection for selected GPIOs."""
            selected = self._get_selected_gpios()
            if not selected:
                # If nothing selected, use the current one (where cursor is)
                if 0 <= self.current_index < len(self.gpio_list):
                    selected = [self.gpio_list[self.current_index]]
            
            if selected:
                self.push_screen(ModeSelectScreen(selected), self._apply_mode)
        
        def _apply_mode(self, mode_key: str) -> None:
            """Apply the selected mode to selected GPIOs."""
            if mode_key:
                selected = self._get_selected_gpios()
                if not selected:
                    # Use current one if nothing selected
                    if 0 <= self.current_index < len(self.gpio_list):
                        selected = [self.gpio_list[self.current_index]]
                
                for gpio_num in selected:
                    real_gpio = self.user_to_real_map[gpio_num]
                    self.gpio_configs[real_gpio] = GPIO_MODES[mode_key]
                    self.gpio_buttons[gpio_num].set_mode(mode_key)
                
                # Clear selection after applying
                for btn in self.gpio_buttons.values():
                    btn.deselect()
                self._update_status()
        
        def action_done(self) -> None:
            """Save and exit."""
            self.exit(result=self.gpio_configs)
        
        def action_quit(self) -> None:
            """Quit without explicit save (but configs are already updated)."""
            self.exit(result=self.gpio_configs)
    
    # Run the Textual grid app
    
    app = GPIOGridApp(gpio_configs, user_to_real_map, user_gpio_range, gpio_label)
    gpio_configs = app.run()
    
    # ========================
    # SUMMARY & SAVE
    # ========================
    console.print("\n[bold]Final Configuration:[/bold]")
    display_summary(gpio_configs, user_to_real_map)
    
    # Check for unconfigured
    unconfigured = [g for g in user_to_real_map.keys() 
                   if not gpio_configs.get(user_to_real_map[g]) or 
                   find_mode_key(gpio_configs.get(user_to_real_map[g])) is None]
    
    if unconfigured:
        console.print(f"\n[yellow]Warning: {len(unconfigured)} pins still unconfigured: {format_gpio_ranges(unconfigured)}[/yellow]")
        confirm = console.input("Save anyway? (y/n): ").strip().lower()
        if confirm not in ('y', 'yes'):
            console.print("[yellow]Aborted.[/yellow]")
            raise click.Abort()
    
    # Save to project.json
    save_gpio_config_to_project_json(str(project_json_path), gpio_configs)
    console.print(f"\n[green]✓ GPIO configuration saved to {project_json_path}[/green]")
    
    # Update user_defines.v
    if not user_defines_path.exists():
        console.print(f"[yellow]Warning: {user_defines_path} not found. Skipping file update.[/yellow]")
    else:
        try:
            update_user_defines_v(str(user_defines_path), gpio_configs)
            console.print(f"[green]✓ Updated {user_defines_path}[/green]")
            
            # Run gen_gpio_defaults.py script after updating user_defines.v
            # DISABLED: Removed by default due to file path issues
            # Look for caravel directory in common locations
            # caravel_paths = [
            #     project_root / 'caravel',
            #     project_root / 'dependencies' / 'caravel',
            #     project_root.parent / 'caravel',  # If caravel is sibling to project
            # ]
            # 
            # gen_gpio_script = None
            # for caravel_path in caravel_paths:
            #     script_path = caravel_path / 'scripts' / 'gen_gpio_defaults.py'
            #     if script_path.exists():
            #         gen_gpio_script = script_path
            #         break
            # 
            # if gen_gpio_script:
            #     try:
            #         console.print("[cyan]Generating GPIO defaults for simulation...[/cyan]")
            #         result = subprocess.run(
            #             [sys.executable, str(gen_gpio_script)],
            #             cwd=str(project_root),
            #             capture_output=True,
            #             text=True,
            #             check=True
            #         )
            #         console.print(f"[green]✓ Generated GPIO defaults[/green]")
            #     except subprocess.CalledProcessError as e:
            #         console.print(f"[yellow]Warning: Failed to run gen_gpio_defaults.py: {e}[/yellow]")
            #         if e.stderr:
            #             console.print(f"[dim]{e.stderr}[/dim]")
            #     except Exception as e:
            #         console.print(f"[yellow]Warning: Error running gen_gpio_defaults.py: {e}[/yellow]")
            # else:
            #     console.print("[dim]Note: gen_gpio_defaults.py not found. Caravel may not be installed yet.[/dim]")
            #     console.print("[dim]Run 'cf setup' to install Caravel, or run the script manually after setup.[/dim]")
        except Exception as e:
            console.print(f"[red]Error updating user_defines.v: {e}[/red]")


@main.command('push')
@click.option('--project-root', required=False, type=click.Path(exists=True, file_okay=False), help='Path to the local ChipFoundry project directory (defaults to current directory if .cf/project.json exists).')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=False, help='SFTP username (defaults to config).')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file (defaults to config).', default=None, show_default=False)
@click.option('--project-id', help='Project ID (e.g., "user123_proj456"). Overrides project.json if exists.')
@click.option('--project-name', help='Project name (e.g., "my_project"). Overrides project.json if exists.')
@click.option('--project-type', help='Project type (auto-detected if not provided).', default=None)
@click.option('--force-overwrite', is_flag=True, help='Overwrite existing files on SFTP without prompting.')
@click.option('--dry-run', is_flag=True, help='Preview actions without uploading files.')
@click.option('--submit', is_flag=True, help='Submit the project for review after upload.')
def push(project_root, sftp_host, sftp_username, sftp_key, project_id, project_name, project_type, force_overwrite, dry_run, submit):
    """Upload your project files to the ChipFoundry SFTP server."""
    # If .cf/project.json exists in cwd, use it as default project_root and project_name
    cwd_root, cwd_project_name = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_name and cwd_project_name:
        project_name = cwd_project_name
    if not project_root:
        console.print("[bold red]No project root specified and no .cf/project.json found in current directory. Please provide --project-root.[/bold red]")
        raise click.Abort()

    # Require platform link and login before pushing
    platform_id = _load_project_platform_id(project_root)
    if not platform_id:
        console.print("[bold red]Project is not linked to the platform.[/bold red]")
        console.print("Run [bold]cf link[/bold] to connect this project, or [bold]cf init[/bold] to create a new one.")
        raise click.Abort()

    # Load user config for defaults
    config = load_user_config()
    api_key = config.get("api_key")
    if not api_key:
        console.print("[bold red]Not logged in.[/bold red]")
        console.print("Run [bold]cf login[/bold] to authenticate before pushing.")
        raise click.Abort()
    if not sftp_username:
        sftp_username = config.get("sftp_username")
        if not sftp_username:
            console.print("[bold red]No SFTP username provided and not found in config. Please run 'chipfoundry init' or provide --sftp-username.[/bold red]")
            raise click.Abort()
    if not sftp_key:
        sftp_key = config.get("sftp_key")
    
    # Always resolve key_path to absolute path if set
    if sftp_key:
        key_path = os.path.abspath(os.path.expanduser(sftp_key))
    else:
        key_path = DEFAULT_SSH_KEY
    
    if not os.path.exists(key_path):
        console.print(f"[red]SFTP key file not found: {key_path}[/red]")
        console.print("[yellow]Please run 'cf keygen' to generate a key or 'cf config' to set a custom key path.[/yellow]")
        raise click.Abort()

    # Collect project files
    try:
        collected = collect_project_files(project_root)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise click.Abort()

    # Auto-detect project type from GDS file name if not provided
    gds_dir = Path(project_root) / 'gds'
    found_types = []
    gds_file_path = None
    for gds_name, gds_type in GDS_TYPE_MAP.items():
        candidate = gds_dir / gds_name
        if candidate.exists():
            found_types.append(gds_type)
            gds_file_path = str(candidate)
    
    # Remove duplicates (compressed and uncompressed files of same type)
    found_types = list(set(found_types))
    
    if project_type:
        detected_type = project_type
    else:
        if len(found_types) == 0:
            console.print("[red]No recognized GDS file found for project type detection.[/red]")
            raise click.Abort()
        elif len(found_types) > 1:
            console.print(f"[red]Multiple GDS types found: {found_types}. Only one project type is allowed per project.[/red]")
            raise click.Abort()
        else:
            detected_type = found_types[0]
    
    # Prepare CLI overrides for project.json
    cli_overrides = {
        "project_id": project_id,
        "project_name": project_name,
        "project_type": detected_type,
        "sftp_username": sftp_username,
    }
    cf_dir = ensure_cf_directory(project_root)
    
    # Find the GDS file path for hash calculation
    gds_path = None
    for gds_key, gds_path in collected.items():
        if gds_key.startswith("gds/"):
            break
    
    project_json_path = update_or_create_project_json(
        cf_dir=str(cf_dir),
        gds_path=gds_path,
        cli_overrides=cli_overrides,
        existing_json_path=collected.get(".cf/project.json")
    )

    # SFTP upload or dry-run
    final_project_name = project_name or (
        cli_overrides.get("project_name") or Path(project_root).name
    )
    sftp_base = f"incoming/projects/{final_project_name}"
    upload_map = {
        ".cf/project.json": project_json_path,
    }
    if detected_type != "openframe":
        upload_map["verilog/rtl/user_defines.v"] = collected.get("verilog/rtl/user_defines.v")

    # Add the appropriate GDS file based on what was collected
    for gds_key, gds_path in collected.items():
        if gds_key.startswith("gds/"):
            upload_map[gds_key] = gds_path
    
    if dry_run:
        console.print("[bold]Files to upload:[/bold]")
        for rel_path, local_path in upload_map.items():
            if local_path:
                remote_path = os.path.join(sftp_base, rel_path)
                console.print(f"  {os.path.basename(local_path)} → {rel_path}")
        return

    console.print(f"Connecting to {sftp_host}...")
    transport = None
    try:
        sftp, transport = sftp_connect(
            host=sftp_host,
            username=sftp_username,
            key_path=key_path
        )
        # Ensure the project directory exists before uploading
        sftp_project_dir = f"incoming/projects/{final_project_name}"
        sftp_ensure_dirs(sftp, sftp_project_dir)
    except Exception as e:
        console.print(f"[red]Failed to connect to SFTP: {e}[/red]")
        raise click.Abort()
    
    try:
        for rel_path, local_path in upload_map.items():
            if local_path:
                remote_path = os.path.join(sftp_base, rel_path)
                upload_with_progress(
                    sftp,
                    local_path=local_path,
                    remote_path=remote_path,
                    force_overwrite=force_overwrite
                )
        console.print(f"[green]✓ Uploaded to {sftp_base}[/green]")
        
    except Exception as e:
        console.print(f"[red]Upload failed: {e}[/red]")
        raise click.Abort()
    finally:
        if transport:
            sftp.close()
            transport.close()

    # --- Platform sync (platform_id and api_key verified at top of push) ---
    try:
        import json as _json
        with open(project_json_path, "r") as f:
            pj = _json.load(f)
        try:
            _api_put(f"/projects/{platform_id}", {"cli_project_json": _slim_project_json(pj), "cli_sync_source": "push"})
            console.print("[green]✓ Platform project synced[/green]")
        except SystemExit:
            console.print("[yellow]⚠ SFTP upload succeeded but platform sync failed[/yellow]")
    except Exception:
        console.print("[yellow]⚠ Could not read project.json for platform sync[/yellow]")

    if submit:
        try:
            _api_post(f"/projects/{platform_id}/submit", {})
            console.print("[green]✓ Project submitted for review[/green]")
        except SystemExit:
            console.print("[yellow]⚠ Submit failed — ensure the project has a name[/yellow]")

@main.command('pull')
@click.option('--project-name', required=False, help='Project name to pull results for (defaults to value in .cf/project.json if present).')
@click.option('--output-dir', required=False, type=click.Path(file_okay=False), help='(Ignored) Local directory to save results (now always sftp-output/<project_name>).')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=False, help='SFTP username (defaults to config).')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file (defaults to config).', default=None, show_default=False)
def pull(project_name, output_dir, sftp_host, sftp_username, sftp_key):
    """Download results/artifacts from SFTP output dir to local sftp-output/<project_name>."""
    # If .cf/project.json exists in cwd, use its project name as default
    _, cwd_project_name = get_project_json_from_cwd()
    if not project_name and cwd_project_name:
        project_name = cwd_project_name
    if not project_name:
        console.print("[bold red]No project name specified and no .cf/project.json found in current directory. Please provide --project-name.[/bold red]")
        raise click.Abort()

    # Require platform link and login before pulling
    platform_id = _load_project_platform_id(".")
    if not platform_id:
        console.print("[bold red]Project is not linked to the platform.[/bold red]")
        console.print("Run [bold]cf link[/bold] to connect this project, or [bold]cf init[/bold] to create a new one.")
        raise click.Abort()

    # Load user config for defaults
    config = load_user_config()
    api_key = config.get("api_key")
    if not api_key:
        console.print("[bold red]Not logged in.[/bold red]")
        console.print("Run [bold]cf login[/bold] to authenticate before pulling.")
        raise click.Abort()
    if not sftp_username:
        sftp_username = config.get("sftp_username")
        if not sftp_username:
            console.print("[bold red]No SFTP username provided and not found in config. Please run 'cf config' or provide --sftp-username.[/bold red]")
            raise click.Abort()
    if not sftp_key:
        sftp_key = config.get("sftp_key")
    
    # Always resolve key_path to absolute path if set
    if sftp_key:
        key_path = os.path.abspath(os.path.expanduser(sftp_key))
    else:
        key_path = DEFAULT_SSH_KEY
    
    if not os.path.exists(key_path):
        console.print(f"[red]SFTP key file not found: {key_path}[/red]")
        console.print("[yellow]Please run 'cf keygen' to generate a key or 'cf config' to set a custom key path.[/yellow]")
        raise click.Abort()

    # Connect to SFTP
    console.print(f"[cyan]Connecting to {sftp_host}...[/cyan]")
    transport = None
    try:
        sftp, transport = sftp_connect(
            host=sftp_host,
            username=sftp_username,
            key_path=key_path
        )
        console.print(f"[green]✓ Connected to {sftp_host}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to connect to SFTP: {e}[/red]")
        raise click.Abort()
    
    try:
        remote_dir = f"outgoing/results/{project_name}"
        output_dir = os.path.join(os.getcwd(), "sftp-output", project_name)
        
        # Check if remote directory exists
        try:
            sftp.stat(remote_dir)
        except Exception:
            console.print(f"[yellow]No results found for project '{project_name}' on SFTP server.[/yellow]")
            return
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Download with progress tracking
        console.print(f"[bold cyan]Downloading project results from {remote_dir}...[/bold cyan]")
        
        try:
            # Use recursive download function with console for clean logging
            sftp_download_recursive(sftp, remote_dir, output_dir, console=console)
            console.print(f"[green]✓ All files downloaded to {output_dir}[/green]")
            
            # Merge pulled project config into local .cf/project.json, preserving platform_project_id
            pulled_config_path = os.path.join(output_dir, "config", "project.json")
            if os.path.exists(pulled_config_path):
                local_config_path = os.path.join(".cf", "project.json")
                os.makedirs(".cf", exist_ok=True)

                try:
                    import json as _json
                    pulled_data = _json.loads(open(pulled_config_path).read())

                    existing_data = {}
                    if os.path.exists(local_config_path):
                        existing_data = _json.loads(open(local_config_path).read())

                    saved_platform_id = existing_data.get("project", {}).get("platform_project_id")

                    merged = pulled_data
                    if saved_platform_id:
                        merged.setdefault("project", {})["platform_project_id"] = saved_platform_id

                    with open(local_config_path, "w") as f:
                        _json.dump(merged, f, indent=2)

                    console.print(f"[green]✓ Project config automatically updated[/green]")
                except Exception as e:
                    console.print(f"[yellow]Warning: Failed to update project config: {e}[/yellow]")
            else:
                console.print(f"[dim]Note: No project config found in pulled results[/dim]")
                
        except Exception as e:
            console.print(f"[red]Failed to download project results: {e}[/red]")
            raise click.Abort()
            
    finally:
        if transport:
            sftp.close()
            transport.close()
            console.print(f"[dim]Disconnected from {sftp_host}[/dim]")

    # --- Platform sync and review notes (platform_id and api_key verified at top of pull) ---
    local_pj = os.path.join(".", ".cf", "project.json")
    if os.path.exists(local_pj):
        try:
            import json as _json
            with open(local_pj, "r") as f:
                pj = _json.load(f)
            _api_put(f"/projects/{platform_id}", {"cli_project_json": _slim_project_json(pj), "cli_sync_source": "pull"})
            console.print("[green]✓ Platform project synced[/green]")
        except SystemExit:
            console.print("[yellow]⚠ SFTP download succeeded but platform sync failed[/yellow]")
        except Exception:
            console.print("[yellow]⚠ Could not read project.json for platform sync[/yellow]")

    try:
        project = _api_get(f"/projects/{platform_id}")
        status = project.get("status", "")
        notes = project.get("admin_review_notes")
        if notes:
            from rich.panel import Panel
            style = "bold red" if status == "CHANGES_REQUESTED" else "yellow"
            console.print()
            console.print(Panel(
                notes,
                title="Review Notes" if status != "CHANGES_REQUESTED" else "Changes Requested",
                border_style=style,
            ))
    except SystemExit:
        pass

STATUS_COLORS = {
    "DRAFT": "dim",
    "SUBMITTED": "yellow",
    "IN_REVIEW": "yellow",
    "CHANGES_REQUESTED": "red",
    "APPROVED": "green",
    "CONFIRMED": "cyan",
    "IN_PRODUCTION": "blue",
    "COMPLETED": "green bold",
    "CANCELLED": "dim red",
}

STATUS_HINTS = {
    "DRAFT": "Run [bold]cf push[/bold] to upload your design files.",
    "APPROVED": "Your project has been approved! Run [bold]cf confirm[/bold] to proceed.",
    "CHANGES_REQUESTED": "Changes requested by the review team. See notes above.",
}


def _show_platform_status(project_root: str):
    """Show the platform pipeline panel if the project is linked. Returns True if shown."""
    platform_id = _load_project_platform_id(project_root)
    if not platform_id:
        return False

    config = load_user_config()
    if not config.get('api_key'):
        return False

    try:
        project = _api_get(f"/projects/{platform_id}")
    except SystemExit:
        console.print("[yellow]⚠ Could not reach the platform API. Showing SFTP status only.[/yellow]\n")
        return False

    status_val = project.get('status', 'UNKNOWN')
    color = STATUS_COLORS.get(status_val, "white")
    portal_url = _get_portal_url()

    lines = []
    lines.append(f"[bold]Name:[/bold]    {project.get('name', 'Unknown')}")
    lines.append(f"[bold]Status:[/bold]  [{color}]{status_val}[/{color}]")
    if project.get('shuttle_name'):
        deadline = ""
        milestones = project.get('shuttle_milestones') or {}
        tapeout = milestones.get('tapeout') if isinstance(milestones, dict) else None
        if tapeout and isinstance(tapeout, dict):
            deadline = f" (deadline: {tapeout.get('milestone_date', 'TBD')})"
        lines.append(f"[bold]Shuttle:[/bold] {project['shuttle_name']}{deadline}")
    if project.get('design_type'):
        lines.append(f"[bold]Type:[/bold]    {project['design_type']}")
    ts_val = project.get('tapeout_state')
    if ts_val:
        _tsl = {"imported": "Imported", "tapeout_running": "Tapeout Running", "tapeout_done": "Tapeout Done", "drc_running": "DRC Running", "drc_checks_clean": "DRC Clean", "drc_checks_done": "DRC Done", "drc_checks_waived": "DRC Waived", "exported": "Exported", "confirmed": "Confirmed"}
        _tsc = {"imported": "dim", "tapeout_running": "blue bold", "tapeout_done": "cyan", "drc_running": "blue bold", "drc_checks_clean": "green", "drc_checks_done": "yellow", "drc_checks_waived": "yellow", "exported": "magenta", "confirmed": "green bold"}
        tl = _tsl.get(ts_val, ts_val)
        tc = _tsc.get(ts_val, "white")
        lines.append(f"[bold]Tapeout:[/bold] [{tc}]{tl}[/{tc}]")
    if project.get('gds_hash'):
        lines.append(f"[bold]GDS Hash:[/bold] {project['gds_hash'][:16]}...")
    if project.get('updated_at'):
        lines.append(f"[bold]Updated:[/bold] {project['updated_at'][:10]}")
    if project.get('admin_review_notes'):
        lines.append(f"\n[bold red]Review Notes:[/bold red] {project['admin_review_notes']}")
    lines.append(f"\n[dim]Portal: {portal_url}/projects/{platform_id}[/dim]")

    hint = STATUS_HINTS.get(status_val)
    if hint:
        lines.append(f"\n[cyan]{hint}[/cyan]")

    panel_text = "\n".join(lines)
    console.print(Panel(panel_text, title="[bold]Platform Project[/bold]", border_style="blue"))
    console.print()
    return True


@main.command('status')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=False, help='SFTP username (defaults to config).')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file (defaults to config).', default=None, show_default=False)
@click.option('--json', 'json_output', is_flag=True, help='Output platform project as JSON.')
@click.option('--all', 'show_all', is_flag=True, help='List all platform projects.')
def status(sftp_host, sftp_username, sftp_key, json_output, show_all):
    """Show project status (platform pipeline + SFTP)."""
    config = load_user_config()

    if json_output:
        platform_id = _load_project_platform_id(os.getcwd())
        if platform_id and config.get('api_key'):
            data = _api_get(f"/projects/{platform_id}")
            console.print_json(json.dumps(data))
        else:
            console.print("[yellow]Not linked to a platform project or not logged in.[/yellow]")
        return

    if show_all:
        if not config.get('api_key'):
            console.print("[yellow]Not logged in.[/yellow] Run [bold]cf login[/bold] to authenticate.")
        else:
            projects = _api_get("/projects/me")
            if not projects:
                console.print("[yellow]No platform projects found.[/yellow]")
            else:
                table = Table(title="Platform Projects")
                table.add_column("Name", style="cyan")
                table.add_column("Shuttle", style="yellow")
                table.add_column("Status", style="green")
                table.add_column("Tapeout")
                table.add_column("Updated", style="dim")
                _tsl = {"imported": "Imported", "tapeout_running": "Tapeout Running", "tapeout_done": "Tapeout Done", "drc_running": "DRC Running", "drc_checks_clean": "DRC Clean", "drc_checks_done": "DRC Done", "drc_checks_waived": "DRC Waived", "exported": "Exported", "confirmed": "Confirmed"}
                _tsc = {"imported": "dim", "tapeout_running": "blue bold", "tapeout_done": "cyan", "drc_running": "blue bold", "drc_checks_clean": "green", "drc_checks_done": "yellow", "drc_checks_waived": "yellow", "exported": "magenta", "confirmed": "green bold"}
                for p in projects:
                    s_color = STATUS_COLORS.get(p.get('status', ''), 'white')
                    ts_raw = p.get('tapeout_state') or ''
                    ts_label = _tsl.get(ts_raw, ts_raw)
                    ts_color = _tsc.get(ts_raw, 'dim')
                    ts_cell = f"[{ts_color}]{ts_label}[/{ts_color}]" if ts_raw else "—"
                    table.add_row(
                        p.get('name', ''),
                        p.get('shuttle_name', '—'),
                        f"[{s_color}]{p.get('status', '')}[/{s_color}]",
                        ts_cell,
                        (p.get('updated_at') or '')[:10],
                    )
                console.print(table)
        console.print()

    shown = _show_platform_status(os.getcwd())
    if not shown and not show_all:
        platform_id = _load_project_platform_id(os.getcwd())
        if not platform_id:
            console.print("[dim]Tip: Run [bold]cf link[/bold] to connect this project to the platform.[/dim]\n")
    if not sftp_username:
        sftp_username = config.get("sftp_username")
        if not sftp_username:
            console.print("[red]No SFTP username provided and not found in config. Please run 'cf config' or provide --sftp-username.[/red]")
            raise click.Abort()
    if not sftp_key:
        sftp_key = config.get("sftp_key")
    
    # Always resolve key_path to absolute path if set
    if sftp_key:
        key_path = os.path.abspath(os.path.expanduser(sftp_key))
    else:
        key_path = DEFAULT_SSH_KEY
    
    if not os.path.exists(key_path):
        console.print(f"[red]SFTP key file not found: {key_path}[/red]")
        console.print("[yellow]Please run 'cf keygen' to generate a key or 'cf config' to set a custom key path.[/yellow]")
        raise click.Abort()

    console.print(f"Connecting to {sftp_host}...")
    transport = None
    try:
        sftp, transport = sftp_connect(
            host=sftp_host,
            username=sftp_username,
            key_path=key_path
        )
    except Exception as e:
        console.print(f"[red]Failed to connect to SFTP: {e}[/red]")
        raise click.Abort()
    try:
        # List projects in incoming/projects/, outgoing/results/, and archive/
        incoming_projects_dir = f"incoming/projects"
        outgoing_results_dir = f"outgoing/results"
        archive_dir = f"archive"
        
        projects = []
        results = []
        archived_projects = []
        
        try:
            projects = sftp.listdir(incoming_projects_dir)
        except Exception:
            pass
        try:
            results = sftp.listdir(outgoing_results_dir)
        except Exception:
            pass
        try:
            archived_items = sftp.listdir(archive_dir)
            # Filter for project directories and parse timestamps
            for item in archived_items:
                if '_' in item and len(item.split('_')) >= 3:
                    # Try to parse timestamp from format like "serial_example_20250813_150354"
                    parts = item.split('_')
                    if len(parts) >= 3:
                        # Check if the last two parts look like date and time
                        date_part = parts[-2]
                        time_part = parts[-1]
                        if len(date_part) == 8 and len(time_part) == 6 and date_part.isdigit() and time_part.isdigit():
                            # This looks like a timestamped archive
                            project_name = '_'.join(parts[:-2])  # Everything except date and time
                            timestamp_str = f"{date_part}_{time_part}"
                            archived_projects.append((project_name, timestamp_str, item))
        except Exception:
            pass
        
        # Create main status table
        table = Table(title=f"SFTP Status for {sftp_username}")
        table.add_column("Project Name", style="cyan", no_wrap=True)
        table.add_column("Has Input", style="yellow")
        table.add_column("Has Output", style="green")
        table.add_column("Last Tapeout Run", style="blue")
        
        # Find the most recent archived project (latest tapeout)
        latest_tapeout = None
        if archived_projects:
            # Sort by timestamp to find the most recent
            archived_projects.sort(key=lambda x: x[1], reverse=True)  # Sort by timestamp descending
            latest_tapeout = archived_projects[0]
            
            # Parse timestamp to human-readable format
            try:
                # timestamp format is "20250813_150354"
                date_part, time_part = latest_tapeout[1].split('_')
                year = date_part[:4]
                month = date_part[4:6]
                day = date_part[6:8]
                hour = time_part[:2]
                minute = time_part[2:4]
                second = time_part[4:6]
                
                formatted_time = f"{year}-{month}-{day} {hour}:{minute}:{second}"
            except:
                formatted_time = latest_tapeout[1]
            
            # Show only the latest tapeout run
            # Check if this project has input and output files
            has_input = "Yes" if latest_tapeout[0] in projects else "No"
            has_output = "Yes" if latest_tapeout[0] in results else "No"
            table.add_row(latest_tapeout[0], has_input, has_output, formatted_time)
        else:
            # No tapeout runs yet, show active projects with their status
            all_projects = set(projects) | set(results)
            for proj in sorted(all_projects):
                has_input = "Yes" if proj in projects else "No"
                has_output = "Yes" if proj in results else "No"
                last_tapeout = "No tapeout yet"
                table.add_row(proj, has_input, has_output, last_tapeout)
        
        if table.row_count > 0:
            console.print(table)
        else:
            console.print("[yellow]No projects or results found on SFTP server.[/yellow]")
            
        # Add informative message about tapeout status
        if not archived_projects and all_projects:
            console.print("\n[cyan]Note: No tapeout runs have started yet. Your projects are waiting in the queue.[/cyan]")
        elif not archived_projects and not all_projects:
            console.print("\n[cyan]Note: No projects found and no tapeout runs have started yet.[/cyan]")
    finally:
        if transport:
            sftp.close()
            transport.close()

@main.command('tapeout-history')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=False, help='SFTP username (defaults to config).')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file (defaults to config).', default=None, show_default=False)
@click.option('--limit', default=50, help='Maximum number of tapeouts to show (default: 50)')
@click.option('--days', default=None, help='Show tapeouts from last N days only')
def tapeouts(sftp_host, sftp_username, sftp_key, limit, days):
    """Show all tapeout runs (archived projects) with their timestamps."""
    config = load_user_config()
    if not sftp_username:
        sftp_username = config.get("sftp_username")
        if not sftp_username:
            console.print("[red]No SFTP username provided and not found in config. Please run 'cf config' or provide --sftp-username.[/red]")
            raise click.Abort()
    if not sftp_key:
        sftp_key = config.get("sftp_key")
    
    # Always resolve key_path to absolute path if set
    if sftp_key:
        key_path = os.path.abspath(os.path.expanduser(sftp_key))
    else:
        key_path = DEFAULT_SSH_KEY
    
    if not os.path.exists(key_path):
        console.print(f"[red]SFTP key file not found: {key_path}[/red]")
        console.print("[yellow]Please run 'cf keygen' to generate a key or 'cf config' to set a custom key path.[/yellow]")
        raise click.Abort()

    console.print(f"Connecting to {sftp_host}...")
    transport = None
    try:
        sftp, transport = sftp_connect(
            host=sftp_host,
            username=sftp_username,
            key_path=key_path
        )
    except Exception as e:
        console.print(f"[red]Failed to connect to SFTP: {e}[/red]")
        raise click.Abort()
    
    try:
        # List archived projects
        archive_dir = f"archive"
        archived_projects = []
        
        try:
            archived_items = sftp.listdir(archive_dir)
            # Filter for project directories and parse timestamps
            for item in archived_items:
                if '_' in item and len(item.split('_')) >= 3:
                    # Try to parse timestamp from format like "serial_example_20250813_150354"
                    parts = item.split('_')
                    if len(parts) >= 3:
                        # Check if the last two parts look like date and time
                        date_part = parts[-2]
                        time_part = parts[-1]
                        if len(date_part) == 8 and len(time_part) == 6 and date_part.isdigit() and time_part.isdigit():
                            # This looks like a timestamped archive
                            project_name = '_'.join(parts[:-2])  # Everything except date and time
                            timestamp_str = f"{date_part}_{time_part}"
                            archived_projects.append((project_name, timestamp_str, item))
        except Exception as e:
            console.print(f"[yellow]Could not access archive directory: {e}[/yellow]")
            return
        
        if not archived_projects:
            console.print("[yellow]No tapeout runs found in archive.[/yellow]")
            return
        
        # Sort by timestamp (most recent first)
        archived_projects.sort(key=lambda x: x[1], reverse=True)
        
        # Apply day filter if specified
        if days:
            from datetime import datetime, timedelta
            cutoff_date = datetime.now() - timedelta(days=days)
            filtered_projects = []
            for proj_name, timestamp, archive_path in archived_projects:
                try:
                    date_part, time_part = timestamp.split('_')
                    year = int(date_part[:4])
                    month = int(date_part[4:6])
                    day = int(date_part[6:8])
                    hour = int(time_part[:2])
                    minute = int(time_part[2:4])
                    second = int(time_part[4:6])
                    
                    archive_datetime = datetime(year, month, day, hour, minute, second)
                    if archive_datetime >= cutoff_date:
                        filtered_projects.append((proj_name, timestamp, archive_path))
                except:
                    # If parsing fails, include it anyway
                    filtered_projects.append((proj_name, timestamp, archive_path))
            
            archived_projects = filtered_projects
            if archived_projects:
                console.print(f"[cyan]Showing tapeouts from last {days} days[/cyan]")
        
        # Apply limit
        if len(archived_projects) > limit:
            console.print(f"[cyan]Showing {limit} most recent tapeouts (use --limit to see more)[/cyan]")
            archived_projects = archived_projects[:limit]
        
        # Create tapeout history table
        table = Table(title=f"Tapeout History for {sftp_username}")
        table.add_column("Project Name", style="cyan", no_wrap=True)
        table.add_column("Tapeout Started", style="green")
        
        for proj_name, timestamp, archive_path in archived_projects:
            # Parse timestamp to human-readable format
            try:
                # timestamp format is "20250813_150354"
                date_part, time_part = timestamp.split('_')
                year = date_part[:4]
                month = date_part[4:6]
                day = date_part[6:8]
                hour = time_part[:2]
                minute = time_part[2:4]
                second = time_part[4:6]
                
                formatted_time = f"{year}-{month}-{day} {hour}:{minute}:{second}"
            except:
                formatted_time = timestamp
            
            table.add_row(proj_name, formatted_time)
        
        console.print(table)
        
        # Show summary
        total_archived = len(archived_projects)
        if total_archived > 0:
            console.print(f"\n[cyan]Total tapeouts shown: {total_archived}[/cyan]")
    
    finally:
        if transport:
            sftp.close()
            transport.close()

@main.command("view-tapeout-report")
@click.option("--project-name", required=False, help="Project name to view tapeout report for (defaults to value in .cf/project.json if present).")
@click.option("--report-path", type=click.Path(exists=True, file_okay=True, dir_okay=False), help="Direct path to the HTML report file.")
def view_tapeout_report(project_name, report_path):
    """View the consolidated tapeout report from the pulled sftp-output directory."""
    if report_path:
        # Use the directly specified report path
        html_path = report_path
    else:
        # Try to find the report based on project name
        if not project_name:
            # Try to get project name from .cf/project.json
            _, cwd_project_name = get_project_json_from_cwd()
            if cwd_project_name:
                project_name = cwd_project_name
            else:
                console.print("[bold red]No project name specified and no .cf/project.json found in current directory. Please provide --project-name or --report-path.[/bold red]")
                raise click.Abort()
        
        # Look for the consolidated report in the expected location
        expected_report_path = os.path.join("sftp-output", project_name, "consolidated_reports", "consolidated_report.html")
        
        if not os.path.exists(expected_report_path):
            console.print(f"[yellow]Tapeout report not found at expected location: {expected_report_path}[/yellow]")
            console.print(f"[cyan]Try running 'cf pull --project-name {project_name}' first to download the report.[/cyan]")
            raise click.Abort()
        
        html_path = expected_report_path
    
    # Open the HTML report in the default browser
    try:
        open_html_in_browser(html_path)
        console.print(f"[green]Opened tapeout report in browser: {html_path}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to open tapeout report in browser: {e}[/red]")
        raise click.Abort()

@main.command('confirm')
@click.option('--project-root', required=False, type=click.Path(exists=True, file_okay=False), help='Path to the local ChipFoundry project directory (defaults to current directory if .cf/project.json exists).')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=False, help='SFTP username (defaults to config).')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file (defaults to config).', default=None, show_default=False)
@click.option('--project-name', help='Project name (e.g., "my_project"). Overrides project.json if exists.')
def confirm(project_root, sftp_host, sftp_username, sftp_key, project_name):
    """Confirm project submission by setting submission_state to Final and pushing project.json to SFTP."""
    # If .cf/project.json exists in cwd, use it as default project_root and project_name
    cwd_root, cwd_project_name = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_name and cwd_project_name:
        project_name = cwd_project_name
    if not project_root:
        console.print("[bold red]No project root specified and no .cf/project.json found in current directory. Please provide --project-root.[/bold red]")
        raise click.Abort()
    
    # Load user config for defaults
    config = load_user_config()
    if not sftp_username:
        sftp_username = config.get("sftp_username")
        if not sftp_username:
            console.print("[bold red]No SFTP username provided and not found in config. Please run 'cf config' or provide --sftp-username.[/bold red]")
            raise click.Abort()
    if not sftp_key:
        sftp_key = config.get("sftp_key")
    
    # Always resolve key_path to absolute path if set
    if sftp_key:
        key_path = os.path.abspath(os.path.expanduser(sftp_key))
    else:
        key_path = DEFAULT_SSH_KEY
    
    if not os.path.exists(key_path):
        console.print(f"[red]SFTP key file not found: {key_path}[/red]")
        console.print("[yellow]Please run 'cf keygen' to generate a key or 'cf config' to set a custom key path.[/yellow]")
        raise click.Abort()

    # Load and update project.json
    project_json_path = Path(project_root) / '.cf' / 'project.json'
    if not project_json_path.exists():
        console.print(f"[red]Project configuration not found at {project_json_path}[/red]")
        console.print("[yellow]Please run 'cf init' first to initialize your project.[/yellow]")
        raise click.Abort()
    
    # Load existing project.json
    try:
        with open(project_json_path, 'r') as f:
            project_data = json.load(f)
    except Exception as e:
        console.print(f"[red]Failed to read project.json: {e}[/red]")
        raise click.Abort()
    
    # Set submission_state to Final
    if "project" not in project_data:
        project_data["project"] = {}
    
    project_data["project"]["submission_state"] = "Final"
    
    # Save updated project.json
    try:
        with open(project_json_path, 'w') as f:
            json.dump(project_data, f, indent=2)
        console.print("[green]✓ Updated project.json with submission_state = Final[/green]")
    except Exception as e:
        console.print(f"[red]Failed to update project.json: {e}[/red]")
        raise click.Abort()
    
    # Get final project name for SFTP upload
    final_project_name = project_name or project_data.get("project", {}).get("name")
    if not final_project_name:
        console.print("[red]No project name found in project.json. Please provide --project-name.[/red]")
        raise click.Abort()
    
    # Connect to SFTP and upload project.json
    console.print(f"Connecting to {sftp_host}...")
    transport = None
    try:
        sftp, transport = sftp_connect(
            host=sftp_host,
            username=sftp_username,
            key_path=key_path
        )
        # Ensure the project directory exists before uploading
        sftp_project_dir = f"incoming/projects/{final_project_name}"
        sftp_ensure_dirs(sftp, sftp_project_dir)
    except Exception as e:
        console.print(f"[red]Failed to connect to SFTP: {e}[/red]")
        raise click.Abort()
    
    try:
        # Upload only the project.json file
        remote_path = os.path.join(sftp_project_dir, ".cf", "project.json")
        upload_with_progress(
            sftp,
            local_path=str(project_json_path),
            remote_path=remote_path,
            force_overwrite=True  # Always overwrite for confirmation
        )
        console.print(f"[green]✓ Confirmed project submission: {final_project_name}[/green]")
        console.print(f"[green]✓ Uploaded project.json to {remote_path}[/green]")
        
    except Exception as e:
        console.print(f"[red]Upload failed: {e}[/red]")
        raise click.Abort()
    finally:
        if transport:
            sftp.close()
            transport.close()

    # --- Platform confirm ---
    platform_id = _load_project_platform_id(project_root or ".")
    confirm_config = load_user_config()
    api_key = confirm_config.get("api_key")

    if platform_id and api_key:
        try:
            _api_post(f"/projects/{platform_id}/confirm", {"confirmation_acknowledged": True})
            console.print("[green]✓ Platform project confirmed[/green]")
        except SystemExit:
            console.print("[yellow]⚠ SFTP confirm succeeded but platform confirm failed — project may need APPROVED status first[/yellow]")
    elif platform_id:
        console.print("[dim]Tip: Run [bold]cf login[/bold] to sync confirmations with the platform[/dim]")

@main.command('setup')
@click.option('--project-root', required=False, type=click.Path(exists=True, file_okay=False), help='Path to the project directory (defaults to current directory).')
@click.option('--repo-owner', default='chipfoundry', help='GitHub repository owner (default: chipfoundry)')
@click.option('--repo-name', default='caravel_user_project', help='GitHub repository name (default: caravel_user_project)')
@click.option('--branch', default='main', help='Branch name (default: main)')
@click.option('--pdk', default='sky130A', type=click.Choice(['sky130A', 'sky130B']), help='PDK variant (default: sky130A)')
@click.option('--caravel-lite/--no-caravel-lite', default=True, help='Install caravel-lite (default) or full caravel')
@click.option('--only-caravel', is_flag=True, help='Only install Caravel')
@click.option('--only-mcw', is_flag=True, help='Only install Management Core Wrapper')
@click.option('--only-openlane', is_flag=True, help='Only install OpenLane/LibreLane')
@click.option('--only-pdk', is_flag=True, help='Only install PDK')
@click.option('--only-timing', is_flag=True, help='Only install timing scripts')
@click.option('--only-cocotb', is_flag=True, help='Only setup Cocotb')
@click.option('--only-precheck', is_flag=True, help='Only install precheck')
@click.option('--overwrite', is_flag=True, help='Overwrite/reinstall even if correct version exists')
@click.option('--dry-run', is_flag=True, help='Preview actions without making changes')
def setup(project_root, repo_owner, repo_name, branch, pdk, caravel_lite, 
          only_caravel, only_mcw, only_openlane, only_pdk, only_timing, only_cocotb, only_precheck, overwrite, dry_run):
    """Set up a ChipFoundry project by installing dependencies.
    
    By default, installs everything. Use --only-* flags to install specific components only.
    This command replaces 'make setup' from the Makefile.
    """
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, cwd_project_name = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    # Check if project is initialized (allow dry-run to proceed)
    check_project_initialized(project_root_path, 'setup', dry_run=dry_run)
    
    # Read project type from project.json
    project_json_path = project_root_path / '.cf' / 'project.json'
    project_type = 'digital'  # default
    if project_json_path.exists():
        try:
            with open(project_json_path, 'r') as f:
                project_data = json.load(f)
            project_type = project_data.get('project', {}).get('type', 'digital')
        except (json.JSONDecodeError, IOError):
            pass  # Use default if we can't read it
    
    is_openframe = project_type == 'openframe'
    
    had_errors = False

    def _error_text(err):
        parts = []
        if isinstance(err, subprocess.CalledProcessError):
            for value in (err.stderr, err.output):
                if value:
                    if isinstance(value, bytes):
                        parts.append(value.decode(errors="ignore"))
                    else:
                        parts.append(str(value))
        parts.append(str(err))
        return "\n".join(parts)

    def maybe_abort_no_space(err, step_label):
        err_text = _error_text(err)
        if getattr(err, "errno", None) == 28 or "No space left on device" in err_text or "Errno 28" in err_text:
            console.print(f"[red]✗[/red] {step_label} failed: No space left on device")
            console.print("[yellow]Free up disk space and rerun `cf setup`.[/yellow]")
            raise click.Abort()
    
    # Determine what to install based on --only-* flags
    only_flags = [only_caravel, only_mcw, only_openlane, only_pdk, only_timing, only_cocotb, only_precheck]
    only_mode = any(only_flags)
    
    # If in "only" mode, only install what's specified
    # If not in "only" mode, install everything
    install_caravel = only_caravel or not only_mode
    # MCW is not used for openframe projects
    install_mcw = (only_mcw or not only_mode) and not is_openframe
    install_openlane = only_openlane or not only_mode
    install_pdk = only_pdk or not only_mode
    install_timing = only_timing or not only_mode
    install_cocotb = only_cocotb or not only_mode
    install_precheck = only_precheck or not only_mode
    
    # Build configuration summary
    config_lines = [
        "[bold cyan]ChipFoundry Project Setup[/bold cyan]\n",
        f"Project directory: [yellow]{project_root}[/yellow]",
        f"Repository: [yellow]{repo_owner}/{repo_name}@{branch}[/yellow]",
        f"PDK: [yellow]{pdk}[/yellow]",
        f"Project type: [yellow]{project_type}[/yellow]",
        f"Caravel variant: [yellow]{'caravel-lite' if caravel_lite else 'caravel'}[/yellow]",
    ]
    
    if is_openframe:
        config_lines.append("[dim]MCW not needed for openframe projects[/dim]")
    
    if only_mode:
        installing = []
        if only_caravel: installing.append("caravel")
        if only_mcw: installing.append("mcw")
        if only_openlane: installing.append("openlane")
        if only_pdk: installing.append("pdk")
        if only_timing: installing.append("timing")
        if only_cocotb: installing.append("cocotb")
        if only_precheck: installing.append("precheck")
        config_lines.append(f"\n[cyan]Installing only: {', '.join(installing)}[/cyan]")
    else:
        config_lines.append("\n[cyan]Installing: All components[/cyan]")
    
    console.print(Panel(
        "\n".join(config_lines),
        title="Setup Configuration",
        expand=False
    ))
    
    if dry_run:
        console.print("[yellow]Dry run mode - no changes will be made[/yellow]\n")
    
    # Fetch versions from upstream
    console.print("[dim]Fetching version information from cf-cli repository...[/dim]")
    try:
        versions = fetch_versions_from_upstream("chipfoundry", "cf-cli", "main")
        mpw_tags = versions['mpw_tags']
        openlane_version = versions['openlane_version']
        open_pdks_commits = versions['open_pdks_commits']
        console.print("[green]✓[/green] Version information loaded successfully")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to fetch version information from cf-cli repository")
        console.print(f"[yellow]Error:[/yellow] {e}")
        console.print("\n[yellow]Please check your internet connection and try again.[/yellow]")
        console.print("[yellow]If the problem persists, please report this issue.[/yellow]")
        raise click.Abort()
    
    # Step 1: Create dependencies directory
    if not only_mode or install_timing or install_caravel:
        console.print("[bold]Step 1:[/bold] Creating dependencies directory...")
        deps_dir = project_root_path / 'dependencies'
        if dry_run:
            console.print(f"[dim]Would create: {deps_dir}[/dim]")
        else:
            deps_dir.mkdir(exist_ok=True)
            console.print(f"[green]✓[/green] Dependencies directory ready at {deps_dir}")
    
    # Step 2: Install Caravel/Caravel-Lite
    if install_caravel:
        console.print("\n[bold]Step 2:[/bold] Installing Caravel...")
        caravel_dir = project_root_path / 'caravel'
        caravel_name = 'caravel-lite' if caravel_lite else 'caravel'
        
        # Determine MPW tag based on PDK
        if pdk not in mpw_tags:
            console.print(f"[red]✗[/red] PDK '{pdk}' not found in version configuration")
            console.print(f"[yellow]Available PDKs: {', '.join(mpw_tags.keys())}[/yellow]")
            raise click.Abort()
        mpw_tag = mpw_tags[pdk]
        
        # Caravel repository URL
        caravel_repo = f'https://github.com/chipfoundry/{caravel_name}'
        
        # Check if already installed with correct version
        is_correct_version, current_version = check_version_installed(caravel_dir, mpw_tag)
        
        if is_correct_version and not overwrite:
            console.print(f"[green]✓[/green] {caravel_name.capitalize()} already installed (version: {current_version})")
        elif dry_run:
            if is_correct_version:
                console.print(f"[dim]Would reinstall: {caravel_repo} (tag: {mpw_tag}) [--overwrite][/dim]")
            else:
                console.print(f"[dim]Would install: {caravel_repo} (tag: {mpw_tag})[/dim]")
        else:
            try:
                if caravel_dir.exists():
                    if current_version:
                        console.print(f"[cyan]Removing existing {caravel_name} (version: {current_version})...[/cyan]")
                    else:
                        console.print(f"[cyan]Removing existing {caravel_dir}...[/cyan]")
                    shutil.rmtree(caravel_dir)
                
                console.print(f"[cyan]Cloning {caravel_name} (tag: {mpw_tag})...[/cyan]")
                result = subprocess.run(
                    ['git', 'clone', '-b', mpw_tag, '--depth=1', caravel_repo, str(caravel_dir)],
                    capture_output=True,
                    text=True,
                    check=True
                )
                console.print(f"[green]✓[/green] {caravel_name.capitalize()} installed successfully")
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, f"{caravel_name.capitalize()} install")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install caravel: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
    
    # Step 3: Install Management Core Wrapper
    # Show message if user explicitly requested MCW but project is openframe
    if only_mcw and is_openframe:
        console.print("\n[bold]Step 3:[/bold] Installing Management Core Wrapper...")
        console.print("[yellow]⚠[/yellow] MCW is not used for openframe projects, skipping...")
    elif install_mcw:
        console.print("\n[bold]Step 3:[/bold] Installing Management Core Wrapper...")
        mcw_dir = project_root_path / 'mgmt_core_wrapper'
        
        # Determine MPW tag and MCW repo based on PDK (from upstream or default)
        mpw_tag = mpw_tags.get(pdk, mpw_tags.get('sky130A', 'CC2509'))
        
        mcw_name = 'mcw-litex-vexriscv'
        mcw_repo = 'https://github.com/chipfoundry/caravel_mgmt_soc_litex'
        
        # Check if already installed with correct version
        is_correct_version, current_version = check_version_installed(mcw_dir, mpw_tag)
        
        if is_correct_version and not overwrite:
            console.print(f"[green]✓[/green] MCW already installed (version: {current_version})")
        elif dry_run:
            if is_correct_version:
                console.print(f"[dim]Would reinstall: {mcw_repo} (tag: {mpw_tag}) [--overwrite][/dim]")
            else:
                console.print(f"[dim]Would install: {mcw_repo} (tag: {mpw_tag})[/dim]")
        else:
            try:
                if mcw_dir.exists():
                    if current_version:
                        console.print(f"[cyan]Removing existing MCW (version: {current_version})...[/cyan]")
                    else:
                        console.print(f"[cyan]Removing existing {mcw_dir}...[/cyan]")
                    shutil.rmtree(mcw_dir)
                
                console.print(f"[cyan]Cloning {mcw_name} (tag: {mpw_tag})...[/cyan]")
                result = subprocess.run(
                    ['git', 'clone', '-b', mpw_tag, '--depth=1', mcw_repo, str(mcw_dir)],
                    capture_output=True,
                    text=True,
                    check=True
                )
                console.print(f"[green]✓[/green] Management Core Wrapper installed successfully")
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "MCW install")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install MCW: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
    
    # Step 4: Install OpenLane/LibreLane
    if install_openlane:
        console.print("\n[bold]Step 4:[/bold] Installing OpenLane/LibreLane...")
        openlane_venv_dir = project_root_path / 'openlane' / '.venv'
        openlane_version_file = project_root_path / 'openlane' / f'.version-{openlane_version}'
        
        # Check if already installed
        is_installed = check_python_package_installed(openlane_venv_dir, 'librelane') and openlane_version_file.exists()
        
        if is_installed and not overwrite:
            console.print(f"[green]✓[/green] OpenLane/LibreLane already installed (version: {openlane_version})")
        elif dry_run:
            if is_installed:
                console.print("[dim]Would reinstall OpenLane/LibreLane [--overwrite][/dim]")
            else:
                console.print("[dim]Would install OpenLane/LibreLane Python virtual environment[/dim]")
        else:
            try:
                # Create openlane directory if it doesn't exist
                openlane_dir = project_root_path / 'openlane'
                openlane_dir.mkdir(exist_ok=True)
                
                # Remove existing venv if overwriting
                if openlane_venv_dir.exists():
                    console.print("[cyan]Removing existing OpenLane venv...[/cyan]")
                    shutil.rmtree(openlane_venv_dir)
                
                console.print("[cyan]Creating OpenLane virtual environment...[/cyan]")
                subprocess.run(
                    [sys.executable, '-m', 'venv', str(openlane_venv_dir)],
                    check=True,
                    capture_output=True
                )
                
                venv_python = str(openlane_venv_dir / 'bin' / 'python3')
                
                console.print("[cyan]Upgrading pip...[/cyan]")
                subprocess.run(
                    [venv_python, '-m', 'pip', 'install', '--upgrade', 'pip'],
                    check=True,
                    capture_output=True
                )
                
                console.print("[cyan]Installing LibreLane...[/cyan]")
                subprocess.run(
                    [venv_python, '-m', 'pip', 'install', 
                     f'https://github.com/chipfoundry/openlane-2/tarball/{openlane_version}'],
                    check=True,
                    capture_output=True
                )
                
                # Save manifest
                console.print("[cyan]Saving package manifest...[/cyan]")
                result = subprocess.run(
                    [venv_python, '-m', 'pip', 'freeze'],
                    capture_output=True,
                    text=True,
                    check=True
                )
                manifest_file = openlane_venv_dir / 'manifest.txt'
                with open(manifest_file, 'w') as f:
                    f.write(result.stdout)
                
                # Create version file
                with open(openlane_version_file, 'w') as f:
                    f.write(f'{openlane_version}\n')
                
                console.print("[green]✓[/green] OpenLane/LibreLane installed successfully")
                console.print("[dim]LibreLane will auto-pull Docker images when needed[/dim]")
                
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "OpenLane setup")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install OpenLane: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
            except Exception as e:
                maybe_abort_no_space(e, "OpenLane setup")
                had_errors = True
                console.print(f"[red]✗[/red] Unexpected error during OpenLane setup: {e}")
    
    # Step 5: Install PDK with Ciel
    if install_pdk:
        console.print("\n[bold]Step 5:[/bold] Installing PDK with Ciel...")
        # Use a dedicated venv location independent of Caravel
        ciel_venv_dir = project_root_path / 'dependencies' / 'ciel-venv'
        pdk_root = project_root_path / 'dependencies' / 'pdks'
        
        # Determine OPEN_PDKS_COMMIT based on PDK
        if pdk not in open_pdks_commits:
            console.print(f"[red]✗[/red] PDK '{pdk}' not found in version configuration")
            console.print(f"[yellow]Available PDKs: {', '.join(open_pdks_commits.keys())}[/yellow]")
            raise click.Abort()
        open_pdks_commit = open_pdks_commits[pdk]
        
        pdk_version_file = pdk_root / f'.version-{open_pdks_commit[:7]}'
        
        # Check if already installed
        is_installed = (
            check_python_package_installed(ciel_venv_dir, 'ciel') and
            pdk_version_file.exists() and
            (pdk_root / pdk).exists()
        )
        
        if is_installed and not overwrite:
            console.print(f"[green]✓[/green] PDK {pdk} already installed (commit: {open_pdks_commit[:7]})")
        elif dry_run:
            if is_installed:
                console.print(f"[dim]Would reinstall PDK {pdk} using Ciel [--overwrite][/dim]")
            else:
                console.print(f"[dim]Would install PDK {pdk} using Ciel[/dim]")
        else:
            try:
                # Ensure dependencies directory exists
                dependencies_dir = project_root_path / 'dependencies'
                dependencies_dir.mkdir(exist_ok=True)
                
                # Remove existing venv if overwriting or doesn't exist
                if ciel_venv_dir.exists() and (overwrite or not is_installed):
                    console.print("[cyan]Removing existing Ciel venv...[/cyan]")
                    shutil.rmtree(ciel_venv_dir)
                
                if not ciel_venv_dir.exists():
                    console.print("[cyan]Creating Ciel virtual environment...[/cyan]")
                    subprocess.run(
                        [sys.executable, '-m', 'venv', str(ciel_venv_dir)],
                        check=True,
                        capture_output=True
                    )
                    
                    venv_python = str(ciel_venv_dir / 'bin' / 'python3')
                    
                    console.print("[cyan]Installing Ciel...[/cyan]")
                    subprocess.run(
                        [venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', 'pip'],
                        check=True,
                        capture_output=True
                    )
                    subprocess.run(
                        [venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', 'ciel'],
                        check=True,
                        capture_output=True
                    )
                    console.print("[green]✓[/green] Ciel installed successfully")
                
                # Remove existing PDK if overwriting
                if (pdk_root / pdk).exists() and overwrite:
                    console.print(f"[cyan]Removing existing PDK {pdk}...[/cyan]")
                    shutil.rmtree(pdk_root / pdk)
                
                if not (pdk_root / pdk).exists():
                    console.print(f"[cyan]Enabling PDK {pdk} with Ciel...[/cyan]")
                    console.print("[dim]Downloading and installing PDK files...[/dim]")
                    
                    # Determine PDK family from PDK variant (sky130A/sky130B -> sky130)
                    pdk_family = pdk.rstrip('AB')  # Remove A or B suffix
                    
                    ciel_bin = str(ciel_venv_dir / 'bin' / 'ciel')
                    
                    # Set up environment with PDK_ROOT
                    env = os.environ.copy()
                    env['PDK_ROOT'] = str(pdk_root)
                    env['CIEL_DATA_SOURCE'] = 'static-web:https://chipfoundry.github.io/ciel-releases'
                    
                    # Run from project root instead of caravel directory
                    result = subprocess.run(
                        [ciel_bin, 'enable', '--pdk-family', pdk_family, open_pdks_commit],
                        cwd=str(project_root_path),
                        env=env,
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    # Verify PDK was actually installed
                    if not (pdk_root / pdk).exists():
                        raise Exception(f"PDK directory {pdk_root / pdk} was not created by Ciel")
                    
                    # Create version file only if PDK exists
                    pdk_root.mkdir(parents=True, exist_ok=True)
                    with open(pdk_version_file, 'w') as f:
                        f.write(f'{open_pdks_commit}\n')
                    
                    console.print("[green]✓[/green] PDK installed successfully")
                    console.print(f"[dim]PDK installed to: {pdk_root}[/dim]")
                
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "PDK install")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install PDK: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
            except Exception as e:
                maybe_abort_no_space(e, "PDK install")
                had_errors = True
                console.print(f"[red]✗[/red] Unexpected error during PDK setup: {e}")
    
    # Step 6: Install timing scripts
    if install_timing:
        step_num = 6 if not only_mode else ""
        console.print(f"\n[bold]Step {step_num}:[/bold] Installing timing scripts...")
        timing_dir = project_root_path / 'dependencies' / 'timing-scripts'
        timing_repo = 'https://github.com/chipfoundry/timing-scripts.git'
        
        # Check if already installed (timing-scripts uses main branch, no version tags)
        is_installed = timing_dir.exists() and (timing_dir / '.git').exists()
        
        if is_installed and not overwrite:
            console.print("[green]✓[/green] Timing scripts already installed")
        elif dry_run:
            if is_installed:
                console.print(f"[dim]Would update: {timing_repo} [--overwrite][/dim]")
            else:
                console.print(f"[dim]Would clone: {timing_repo}[/dim]")
        else:
            try:
                if timing_dir.exists():
                    if overwrite:
                        console.print("[cyan]Updating existing timing-scripts...[/cyan]")
                        result = subprocess.run(
                            ['git', 'pull'],
                            cwd=str(timing_dir),
                            capture_output=True,
                            text=True,
                            check=True
                        )
                        console.print("[green]✓[/green] Timing scripts updated")
                else:
                    # Ensure dependencies directory exists
                    timing_dir.parent.mkdir(parents=True, exist_ok=True)
                    console.print("[cyan]Cloning timing-scripts...[/cyan]")
                    result = subprocess.run(
                        ['git', 'clone', timing_repo, str(timing_dir)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    console.print("[green]✓[/green] Timing scripts installed")
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "Timing scripts install")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install timing scripts: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
    
    # Step 7: Set up Cocotb
    if install_cocotb:
        step_num = 7 if not only_mode else ""
        console.print(f"\n[bold]Step {step_num}:[/bold] Setting up Cocotb...")
        venv_cocotb = project_root_path / 'venv-cocotb'
        
        # Check if already installed (package name is caravel_cocotb in pyproject.toml)
        is_installed = check_python_package_installed(venv_cocotb, 'caravel_cocotb')
        
        if is_installed and not overwrite:
            console.print("[green]✓[/green] Cocotb already installed")
        elif dry_run:
            if is_installed:
                console.print("[dim]Would reinstall Cocotb virtual environment [--overwrite][/dim]")
            else:
                console.print("[dim]Would create Cocotb virtual environment and install dependencies[/dim]")
        else:
            try:
                # Remove existing venv-cocotb if overwriting
                if venv_cocotb.exists() and overwrite:
                    console.print("[cyan]Removing existing venv-cocotb...[/cyan]")
                    shutil.rmtree(venv_cocotb)
                
                if not venv_cocotb.exists():
                    console.print("[cyan]Creating Cocotb virtual environment...[/cyan]")
                    subprocess.run(
                        [sys.executable, '-m', 'venv', str(venv_cocotb)],
                        check=True,
                        capture_output=True
                    )
                    
                    # Determine the python executable path in venv
                    venv_python = str(venv_cocotb / 'bin' / 'python3')
                    
                    console.print("[cyan]Installing caravel-cocotb from source (chipfoundry/caravel-sim-infrastructure)...[/cyan]")
                    subprocess.run(
                        [venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', 'pip'],
                        check=True,
                        capture_output=True
                    )
                    # Install from GitHub source (workaround: PyPI package is under efabless, we use chipfoundry repo)
                    subprocess.run(
                        [
                            venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir',
                            'git+https://github.com/chipfoundry/caravel-sim-infrastructure.git@main#subdirectory=cocotb'
                        ],
                        check=True,
                        capture_output=True
                    )
                    console.print("[green]✓[/green] Cocotb environment set up successfully")
                
                # Run setup-cocotb.py to configure paths
                console.print("[cyan]Configuring Cocotb paths...[/cyan]")
                setup_cocotb_script = project_root_path / 'verilog' / 'dv' / 'setup-cocotb.py'
                if setup_cocotb_script.exists():
                    # setup-cocotb.py requires PyYAML
                    subprocess.run(
                        [venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', 'pyyaml'],
                        check=True,
                        capture_output=True
                    )
                    caravel_root = project_root_path / 'caravel'
                    mcw_root = project_root_path / 'mgmt_core_wrapper'
                    pdk_root = project_root_path / 'dependencies' / 'pdks'
                    
                    subprocess.run(
                        [venv_python, str(setup_cocotb_script),
                         str(caravel_root), str(mcw_root), str(pdk_root), pdk, str(project_root_path)],
                        check=True,
                        capture_output=True
                    )
                    console.print("[green]✓[/green] Cocotb paths configured")
                else:
                    console.print("[yellow]⚠[/yellow] setup-cocotb.py not found, skipping path configuration")
                
                # Pull cocotb docker image
                console.print("[cyan]Pulling Cocotb Docker image...[/cyan]")
                subprocess.run(
                    ['docker', 'pull', 'chipfoundry/dv:cocotb'],
                    check=True,
                    capture_output=True
                )
                console.print("[green]✓[/green] Cocotb Docker image ready")
                
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "Cocotb setup")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to set up Cocotb: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
            except Exception as e:
                maybe_abort_no_space(e, "Cocotb setup")
                had_errors = True
                console.print(f"[red]✗[/red] Unexpected error during Cocotb setup: {e}")
    
    # Step 8: Install precheck
    if install_precheck:
        step_num = 8 if not only_mode else ""
        console.print(f"\n[bold]Step {step_num}:[/bold] Installing precheck...")
        
        if dry_run:
            console.print("[dim]Would install/upgrade cf-precheck Python package[/dim]")
        else:
            try:
                console.print("[cyan]Installing cf-precheck...[/cyan]")
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '--upgrade', '-q', 'cf-precheck'],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                try:
                    result = subprocess.run(
                        [sys.executable, '-c', 'from cf_precheck import __version__; print(__version__)'],
                        capture_output=True, text=True,
                    )
                    version = result.stdout.strip()
                    console.print(f"[green]✓[/green] cf-precheck v{version} installed")
                except Exception:
                    console.print("[green]✓[/green] cf-precheck installed")
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "Precheck install")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install cf-precheck: {e}")
                if e.stderr:
                    console.print(f"[dim]{e.stderr}[/dim]")
    
    # Summary
    console.print("\n" + "="*60)
    if dry_run:
        console.print("[bold yellow]Dry run complete![/bold yellow] No changes were made.")
    else:
        if had_errors:
            console.print("[bold yellow]Setup completed with errors.[/bold yellow] Review messages above.")
        elif only_mode:
            console.print("[bold green]Installation complete![/bold green]")
        else:
            console.print("[bold green]Setup complete![/bold green]")
    
@main.command('harden')
@click.argument('macro', required=False)
@click.option('--project-root', type=click.Path(exists=True, file_okay=False), help='Path to the project directory (defaults to current directory)')
@click.option('--list', 'list_designs', is_flag=True, help='List all available macros')
@click.option('--tag', help='Custom run tag (defaults to timestamp)')
@click.option('--pdk', help='PDK to use (defaults to sky130A)')
@click.option('--use-nix', is_flag=True, help='Force use of Nix (fails if Nix not available)')
@click.option('--use-docker', is_flag=True, help='Force use of Docker (fails if Docker not available)')
@click.option('--dry-run', is_flag=True, help='Show the configuration without running')
def harden(macro, project_root, list_designs, tag, pdk, use_nix, use_docker, dry_run):
    """Harden a macro using LibreLane (OpenLane 2).
    
    Examples:
        cf harden user_proj_example   # Harden a specific macro
        cf harden user_project_wrapper
        cf harden --list              # List available macros
    """
    from datetime import datetime
    
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    # Check if project is initialized (skip check for --list or when no macro specified, allow graceful return)
    if not list_designs and macro:
        if not check_project_initialized(project_root_path, 'harden', dry_run=dry_run, allow_graceful=True):
            console.print(f"[red]✗[/red] Project not initialized. Please run 'cf init' first.")
            console.print("[yellow]Run 'cf setup' first to install OpenLane[/yellow]")
            return
    
    openlane_dir = project_root_path / 'openlane'
    
    # Check if openlane directory exists
    if not openlane_dir.exists():
        console.print(f"[red]✗[/red] OpenLane directory not found: {openlane_dir}")
        console.print("[yellow]Run 'cf setup' first to install OpenLane[/yellow]")
        return
    
    # If no macro specified, show prompt with available macros
    no_macro_specified = not macro and not list_designs
    if not macro:
        list_designs = True
    
    # List designs if requested (or if no macro specified)
    if list_designs:
        if no_macro_specified:
            console.print("[yellow]Please specify a macro from this list:[/yellow]")
        else:
            console.print("[bold cyan]Available macros:[/bold cyan]")
        designs = [d.name for d in openlane_dir.iterdir() if d.is_dir() and ((d / 'config.json').exists() or (d / 'config.yaml').exists() or (d / 'config.tcl').exists())]
        if designs:
            for design in sorted(designs):
                config_file = None
                for ext in ['json', 'yaml', 'tcl']:
                    config_path = openlane_dir / design / f'config.{ext}'
                    if config_path.exists():
                        config_file = f'config.{ext}'
                        break
                console.print(f"  • {design} ({config_file})")
        else:
            console.print("[yellow]No macros found in openlane/[/yellow]")
        return
    
    # Check if macro exists
    macro_dir = openlane_dir / macro
    if not macro_dir.exists():
        console.print(f"[red]✗[/red] Macro not found: {macro}")
        console.print(f"[yellow]Run 'cf harden --list' to see available macros[/yellow]")
        return
    
    # Find config file
    config_file = None
    for ext in ['json', 'yaml', 'tcl']:
        config_path = macro_dir / f'config.{ext}'
        if config_path.exists():
            config_file = str(config_path)
            break
    
    if not config_file:
        console.print(f"[red]✗[/red] No config file found for {macro}")
        console.print(f"[yellow]Expected one of: config.json, config.yaml, config.tcl[/yellow]")
        return
    
    # Check for LibreLane venv
    librelane_venv = openlane_dir / '.venv'
    if not librelane_venv.exists():
        console.print("[red]✗[/red] LibreLane not installed")
        console.print("[yellow]Run 'cf setup --only-openlane' to install LibreLane[/yellow]")
        raise click.Abort()
    
    # Fetch versions from upstream
    console.print("[dim]Fetching version information from cf-cli repository...[/dim]")
    try:
        versions = fetch_versions_from_upstream("chipfoundry", "cf-cli", "main")
        openlane_version = versions['openlane_version']
        console.print("[green]✓[/green] Version information loaded successfully")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to fetch version information from cf-cli repository")
        console.print(f"[yellow]Error:[/yellow] {e}")
        console.print("\n[yellow]Please check your internet connection and try again.[/yellow]")
        console.print("[yellow]If the problem persists, please report this issue.[/yellow]")
        raise click.Abort()
    
    # Detect available execution method: Nix > Docker > Error
    force_nix_flag = use_nix
    force_docker_flag = use_docker
    use_nix = False
    use_docker = False
    
    # Check for conflicting flags
    if force_nix_flag and force_docker_flag:
        console.print("[red]✗[/red] Cannot use both --use-nix and --use-docker")
        return
    
    # Check if Nix is available
    if force_nix_flag or not force_docker_flag:
        nix_available = shutil.which('nix') is not None
        if nix_available:
            # Check if LibreLane is accessible via Nix
            try:
                result = subprocess.run(
                    ['nix', 'flake', 'metadata', f'github:chipfoundry/openlane-2/{openlane_version}', '--json'],
                    capture_output=True,
                    timeout=5
                )
                use_nix = result.returncode == 0
            except:
                pass
        
        if force_nix_flag and not use_nix:
            console.print("[red]✗[/red] Nix not available or cannot access LibreLane flake")
            console.print("[yellow]Install Nix from: https://librelane.readthedocs.io[/yellow]")
            raise click.Abort()
    
    # Check if Docker is available
    if not use_nix and (force_docker_flag or not force_nix_flag):
        try:
            result = subprocess.run(
                ['docker', 'info'],
                capture_output=True,
                timeout=5
            )
            use_docker = result.returncode == 0
        except:
            pass
        
        if force_docker_flag and not use_docker:
            console.print("[red]✗[/red] Docker not available")
            console.print("[yellow]Install Docker from: https://docker.com[/yellow]")
            raise click.Abort()
    
    # Error if neither is available
    if not use_nix and not use_docker:
        console.print("[red]✗[/red] Neither Nix nor Docker is available")
        console.print("\n[yellow]LibreLane requires either:[/yellow]")
        console.print("  1. [cyan]Nix[/cyan] - Install from: https://librelane.readthedocs.io")
        console.print("  2. [cyan]Docker[/cyan] - Install from: https://docker.com")
        console.print("\nAfter installing either one, try again.")
        raise click.Abort()
    
    execution_method = "Nix" if use_nix else "Docker"
    
    # Set up environment variables
    pdk_root = project_root_path / 'dependencies' / 'pdks'
    
    if not pdk:
        # Try to detect PDK from project.json
        project_json_path = project_root_path / '.cf' / 'project.json'
        if project_json_path.exists():
            try:
                with open(project_json_path, 'r') as f:
                    project_data = json.load(f)
                    pdk = project_data.get('pdk', 'sky130A')
            except:
                pdk = 'sky130A'
        else:
            pdk = 'sky130A'
    
    # Verify PDK is installed
    pdk_dir = pdk_root / pdk
    if not pdk_dir.exists():
        console.print(f"[red]✗[/red] PDK not found: {pdk_dir}")
        console.print("[yellow]Run 'cf setup --only-pdk' to install the PDK[/yellow]")
        return
    
    if not tag:
        tag = datetime.now().strftime('%y_%m_%d_%H_%M')
    
    # Display configuration
    console.print("\n" + "="*60)
    console.print(f"[bold cyan]Hardening: {macro}[/bold cyan]")
    console.print(f"Config: [yellow]{Path(config_file).name}[/yellow]")
    console.print(f"Run tag: [yellow]{tag}[/yellow]")
    console.print(f"PDK: [yellow]{pdk}[/yellow]")
    console.print(f"PDK Root: [yellow]{pdk_root}[/yellow]")
    console.print(f"Execution: [yellow]{execution_method}[/yellow]")
    console.print("="*60 + "\n")
    
    if dry_run:
        console.print("[bold yellow]Dry run - configuration ready[/bold yellow]")
        console.print(f"Would use: {execution_method}")
        return
    
    # Build command based on execution method
    if use_nix:
        # Use Nix to run LibreLane
        console.print(f"[cyan]Running LibreLane via Nix on {macro}...[/cyan]")
        
        cmd = [
            'nix', 'run', f'github:chipfoundry/openlane-2/{openlane_version}', '--',
            '--run-tag', tag,
            '--manual-pdk',
            '--pdk-root', str(pdk_root),
            '--pdk', pdk,
            '--ef-save-views-to', str(project_root_path),
            '--overwrite',
            config_file
        ]
        
        env = os.environ.copy()
        env.update({
            'PROJECT_ROOT': str(project_root_path),
            'PDK_ROOT': str(pdk_root),
            'PDK': pdk,
            'LIBRELANE_RUN_TAG': tag,
        })
        
    else:
        # Use Docker via venv
        console.print(f"[cyan]Running LibreLane via Docker on {macro}...[/cyan]")
        
        # Set up environment for LibreLane
        env = os.environ.copy()
        env.update({
            'PROJECT_ROOT': str(project_root_path),
            'PDK_ROOT': str(pdk_root),
            'PDK': pdk,
            'LIBRELANE_RUN_TAG': tag,
            'PYTHONPATH': str(librelane_venv / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages')
        })
        
        # Add venv to PATH so librelane can find its dependencies
        venv_bin = librelane_venv / 'bin'
        env['PATH'] = f"{venv_bin}:{env.get('PATH', '')}"
        
        # Build LibreLane command
        # Note: When using --dockerized, LibreLane reads PDK settings from environment variables
        cmd = [
            str(venv_bin / 'python3'), '-m', 'librelane',
            '-m', str(project_root_path),
            '-m', str(pdk_root),
            '--dockerized',
        ]
        
        # Add --docker-no-tty if not running in a TTY (e.g., CI environments)
        try:
            if not sys.stdin.isatty():
                cmd.append('--docker-no-tty')
        except:
            # If we can't detect TTY, assume non-TTY (safer for CI)
            cmd.append('--docker-no-tty')
        
        cmd.extend([
            '--run-tag', tag,
            '--manual-pdk',
            '--pdk-root', str(pdk_root),
            '--pdk', pdk,
            '--ef-save-views-to', str(project_root_path),
            '--overwrite',
            config_file
        ])
    
    # Run LibreLane
    
    try:
        # Use Popen for better signal handling
        process = subprocess.Popen(
            cmd,
            cwd=str(openlane_dir),
            env=env,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        # Wait for process to complete
        returncode = process.wait()
        
        if returncode == 0:
            console.print(f"\n[green]✓[/green] [bold green]Successfully hardened {macro}![/bold green]")
            console.print(f"[dim]Results saved to: {project_root_path}/runs/{macro}/{tag}/[/dim]")
        elif returncode == -2 or returncode == 130:  # SIGINT
            console.print("\n[yellow]⚠[/yellow] Hardening interrupted by user")
            sys.exit(130)
        else:
            console.print(f"\n[red]✗[/red] [bold red]Hardening failed with exit code {returncode}[/bold red]")
            console.print(f"[yellow]Check logs in: {project_root_path}/runs/{macro}/{tag}/[/yellow]")
            sys.exit(returncode)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Hardening interrupted by user")
        # Try to stop the process group gracefully
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
            else:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {e}")

@main.group('repo')
def repo_group():
    """Repository management commands."""
    pass

@repo_group.command('update')
@click.option('--project-root', required=False, type=click.Path(exists=True, file_okay=False), help='Path to the local ChipFoundry project directory (defaults to current directory if .cf/project.json exists).')
@click.option('--repo-owner', default='chipfoundry', help='GitHub repository owner (default: chipfoundry)')
@click.option('--repo-name', default='caravel_user_project', help='GitHub repository name (default: caravel_user_project)')
@click.option('--branch', default='main', help='Branch name containing the repo.json file (default: main)')
@click.option('--dry-run', is_flag=True, help='Preview changes without updating files')
def repo_update(project_root, repo_owner, repo_name, branch, dry_run):
    """Update local repository files from upstream GitHub repository based on .cf/repo.json changes list."""
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    console.print(f"[bold cyan]Updating repository files from {repo_owner}/{repo_name}@{branch}[/bold cyan]")
    
    try:
        if dry_run:
            console.print("[yellow]Dry run mode - no files will be modified[/yellow]")
            # Fetch repo.json to show what would be updated
            from chipfoundry_cli.utils import fetch_github_file
            repo_json_content = fetch_github_file(repo_owner, repo_name, ".cf/repo.json", branch)
            repo_data = json.loads(repo_json_content)
            changes = repo_data.get("changes", [])
            
            console.print(f"[cyan]Files that would be updated:[/cyan]")
            console.print(f"  • .cf/repo.json (configuration file)")
            for file_path in changes:
                console.print(f"  • {file_path}")
        else:
            # Perform the actual update
            results = update_repo_files(project_root, repo_owner, repo_name, branch)
            
            if "error" in results:
                console.print(f"[red]Failed to fetch repository information: {results['error']}[/red]")
                raise click.Abort()
            
            # Display results
            success_count = 0
            failure_count = 0
            
            console.print(f"[cyan]Update results:[/cyan]")
            for file_path, success in results.items():
                if success:
                    console.print(f"[green]✓ Updated: {file_path}[/green]")
                    success_count += 1
                else:
                    console.print(f"[red]✗ Failed: {file_path}[/red]")
                    failure_count += 1
            
            if success_count > 0:
                console.print(f"[green]Successfully updated {success_count} file(s)[/green]")
            if failure_count > 0:
                console.print(f"[red]Failed to update {failure_count} file(s)[/red]")
                raise click.Abort()
            else:
                console.print("[green]All files updated successfully![/green]")
                
    except Exception as e:
        console.print(f"[red]Repository update failed: {e}[/red]")
        raise click.Abort()


@main.command('precheck')
@click.option('--project-root', type=click.Path(exists=True, file_okay=False), help='Path to the project directory (defaults to current directory)')
@click.option('--skip-checks', multiple=True, help='Checks to skip (can be specified multiple times)')
@click.option('--magic-drc', is_flag=True, help='Include Magic DRC check (optional, off by default)')
@click.option('--checks', multiple=True, help='Specific checks to run (can be specified multiple times)')
@click.option('--dry-run', is_flag=True, help='Show the command without running')
def precheck(project_root, skip_checks, magic_drc, checks, dry_run):
    """Run precheck validation on the project.
    
    This runs the cf-precheck tool to validate your design before
    submission.
    
    Examples:
        cf precheck                              # Run all checks
        cf precheck --skip-checks lvs            # Skip LVS check
        cf precheck --magic-drc                  # Include optional Magic DRC
        cf precheck --checks topcell_check       # Run specific checks only
    """
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    if not check_project_initialized(project_root_path, 'precheck', dry_run=dry_run, allow_graceful=True):
        console.print(f"[red]✗[/red] Project not initialized. Please run 'cf init' first.")
        console.print("[yellow]Dependencies are required before running precheck.[/yellow]")
        return
    
    project_json_path = project_root_path / '.cf' / 'project.json'
    
    with open(project_json_path, 'r') as f:
        project_data = json.load(f)
    project_type = project_data.get('project', {}).get('type', 'digital')
    
    if project_type != 'openframe':
        gpio_config = get_gpio_config_from_project_json(str(project_json_path))
        if not gpio_config or len(gpio_config) == 0:
            console.print("[red]✗[/red] GPIO configuration not found in project.json")
            console.print("[yellow]GPIO configuration is required before running precheck.[/yellow]")
            console.print("[cyan]Please run 'cf gpio-config' to configure GPIO settings first.[/cyan]")
            raise click.Abort()
    
    pdk_root = project_root_path / 'dependencies' / 'pdks'
    
    pdk = 'sky130A'
    if project_json_path.exists():
        try:
            with open(project_json_path, 'r') as f:
                project_data = json.load(f)
                pdk = project_data.get('pdk', 'sky130A')
        except:
            pass
    
    if not (pdk_root / pdk).exists():
        console.print(f"[red]✗[/red] PDK not found at {pdk_root / pdk}")
        console.print("[yellow]Run 'cf setup --only-pdk' to install[/yellow]")
        return
    
    if shutil.which('docker') is None:
        console.print("[red]✗[/red] Docker not found. Docker is required to run precheck.")
        return
    
    pdk_path = pdk_root / pdk
    
    docker_image = 'chipfoundry/mpw_precheck:latest'
    
    precheck_args = [
        '-i', str(project_root_path),
        '-p', str(pdk_path),
        '-c', '/opt/caravel',
    ]
    
    if magic_drc:
        precheck_args.append('--magic-drc')
    
    if skip_checks:
        precheck_args.extend(['--skip-checks'] + list(skip_checks))
    
    if checks:
        precheck_args.extend(list(checks))
    
    inner_cmd = 'pip3 install --upgrade -q cf-precheck && cf-precheck ' + ' '.join(precheck_args)
    
    docker_cmd = [
        'docker', 'run', '--rm',
        '-v', f'{project_root_path}:{project_root_path}',
        '-v', f'{pdk_root}:{pdk_root}',
        docker_image,
        'bash', '-c', inner_cmd,
    ]
    
    checks_display = ', '.join(checks) if checks else 'All checks'
    console.print("\n" + "="*60)
    console.print("[bold cyan]CF Precheck[/bold cyan]")
    console.print(f"Project: [yellow]{project_root_path}[/yellow]")
    console.print(f"PDK: [yellow]{pdk}[/yellow]")
    if skip_checks:
        console.print(f"Skipping: [yellow]{', '.join(skip_checks)}[/yellow]")
    if magic_drc:
        console.print("Magic DRC: [yellow]enabled[/yellow]")
    console.print(f"Checks: [yellow]{checks_display}[/yellow]")
    console.print("="*60 + "\n")
    
    if dry_run:
        console.print("[bold yellow]Dry run - would execute:[/bold yellow]\n")
        console.print("[dim]" + ' '.join(docker_cmd) + "[/dim]")
        return
    
    # Pull/update Docker image before running
    console.print(f"[cyan]Checking for Docker image updates...[/cyan]")
    try:
        subprocess.run(
            ['docker', 'pull', docker_image],
            check=True,
            capture_output=True,
        )
        console.print(f"[green]✓[/green] Docker image up to date")
    except subprocess.CalledProcessError:
        # Image might already be available locally, warn but continue
        result = subprocess.run(
            ['docker', 'image', 'inspect', docker_image],
            capture_output=True,
        )
        if result.returncode != 0:
            console.print(f"[red]✗[/red] Docker image '{docker_image}' not found. Run 'cf setup --only-precheck' or check your connection.")
            return
        console.print("[yellow]⚠[/yellow] Could not check for image updates (using cached image)")
    
    console.print("[cyan]Running cf-precheck...[/cyan]\n")
    
    try:
        process = subprocess.Popen(
            docker_cmd,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        returncode = process.wait()
        
        console.print("")
        if returncode == 0:
            console.print("[green]✓[/green] Precheck passed!")
        elif returncode == -2 or returncode == 130:
            console.print("[yellow]⚠[/yellow] Precheck interrupted by user")
            sys.exit(130)
        else:
            console.print(f"[red]✗[/red] Precheck failed (exit code {returncode})")
            sys.exit(returncode)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Precheck interrupted by user")
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
            else:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
    except Exception as e:
        console.print(f"\n[red]✗[/red] Error running precheck: {e}")

@main.command('verify')
@click.argument('test', required=False)
@click.option('--project-root', type=click.Path(exists=True, file_okay=False), help='Path to the project directory (defaults to current directory)')
@click.option('--sim', type=click.Choice(['rtl', 'gl'], case_sensitive=False), default='rtl', help='Simulation type: rtl or gl (gate-level)')
@click.option('--list', 'list_tests', is_flag=True, help='List all available cocotb tests')
@click.option('--all', 'run_all', is_flag=True, help='Run all tests')
@click.option('--tag', help='Test list tag/yaml file (e.g., all_tests or user_proj_tests)')
@click.option('--dry-run', is_flag=True, help='Show the configuration without running')
def verify(test, project_root, sim, list_tests, run_all, tag, dry_run):
    """Run cocotb verification tests.
    
    Examples:
        cf verify --list                    # List all available tests
        cf verify counter_la                # Run a specific test (RTL)
        cf verify counter_la --sim gl       # Run gate-level simulation
        cf verify --all                     # Run all tests
        cf verify --tag all_tests           # Run tests from a yaml list
    """
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    # Check if project is initialized (skip check if just listing tests, allow graceful return)
    if not list_tests:
        if not check_project_initialized(project_root_path, 'verify', dry_run=dry_run, allow_graceful=True):
            console.print(f"[red]✗[/red] Project not initialized. Please run 'cf init' first.")
            console.print("[yellow]Cocotb tests require project initialization.[/yellow]")
            return
    
    project_json_path = project_root_path / '.cf' / 'project.json'
    
    # Get project type (needed for openframe flag)
    with open(project_json_path, 'r') as f:
        project_data = json.load(f)
    project_type = project_data.get('project', {}).get('type', 'digital')
    
    # Check if GPIO configuration exists (skip check if just listing tests or openframe)
    if not list_tests:
        if project_type != 'openframe':
            gpio_config = get_gpio_config_from_project_json(str(project_json_path))
            if not gpio_config or len(gpio_config) == 0:
                console.print("[red]✗[/red] GPIO configuration not found in project.json")
                console.print("[yellow]GPIO configuration is required before running verification.[/yellow]")
                console.print("[cyan]Please run 'cf gpio-config' to configure GPIO settings first.[/cyan]")
                raise click.Abort()
    
    cocotb_dir = project_root_path / 'verilog' / 'dv' / 'cocotb'
    venv_cocotb = project_root_path / 'venv-cocotb'
    
    # Check if cocotb directory exists
    if not cocotb_dir.exists():
        console.print(f"[red]✗[/red] Cocotb directory not found: {cocotb_dir}")
        console.print("[yellow]This project may not have cocotb tests set up.[/yellow]")
        return
    
    # Check if caravel-cocotb is installed
    if not (venv_cocotb / 'bin' / 'caravel_cocotb').exists():
        console.print(f"[red]✗[/red] caravel_cocotb not found in {venv_cocotb}")
        console.print("[yellow]Run 'cf setup --only-cocotb' to install cocotb[/yellow]")
        return
    
    # Find available tests
    available_tests = []
    available_yaml_files = []
    
    for item in cocotb_dir.rglob('*.yaml'):
        yaml_name = item.stem
        # Skip design_info.yaml and test list yamls at root of test dirs
        if yaml_name not in ['design_info', 'user_proj_tests', 'user_proj_tests_gl']:
            # Individual test yamls
            available_tests.append(yaml_name)
        else:
            # Test list yamls
            available_yaml_files.append(item.relative_to(cocotb_dir))
    
    if list_tests:
        console.print("[bold green]Available cocotb tests:[/bold green]")
        console.print("\n[cyan]Individual tests:[/cyan]")
        for t in sorted(set(available_tests)):
            console.print(f"  • {t}")
        
        console.print("\n[cyan]Test lists (use with --tag):[/cyan]")
        for f in sorted(available_yaml_files):
            console.print(f"  • {f.parent.name}/{f.name}" if f.parent.name != '.' else f" • {f.name}")
        return
    
    # Determine what to run
    if not test and not run_all and not tag:
        console.print("[red]Error: Specify a test name, use --all, or --tag <test_list>[/red]")
        console.print("Use 'cf verify --list' to see available tests")
        return
    
    # Set up environment variables
    caravel_root = project_root_path / 'caravel'
    mcw_root = project_root_path / 'mgmt_core_wrapper'
    pdk_root = project_root_path / 'dependencies' / 'pdks'
    
    # Detect PDK from project.json
    pdk = 'sky130A'
    project_json_path = project_root_path / '.cf' / 'project.json'
    if project_json_path.exists():
        try:
            with open(project_json_path, 'r') as f:
                project_data = json.load(f)
                pdk = project_data.get('pdk', 'sky130A')
        except:
            pass
    
    # Check required paths exist
    if not caravel_root.exists():
        console.print(f"[red]✗[/red] Caravel not found at {caravel_root}")
        console.print("[yellow]Run 'cf setup --only-caravel' to install[/yellow]")
        sys.exit(1)
    
    if not (pdk_root / pdk).exists():
        console.print(f"[red]✗[/red] PDK not found at {pdk_root / pdk}")
        console.print("[yellow]Run 'cf setup --only-pdk' to install[/yellow]")
        sys.exit(1)
    
    # Build command
    caravel_cocotb_bin = venv_cocotb / 'bin' / 'caravel_cocotb'
    sim_arg = 'GL' if sim.lower() == 'gl' else 'RTL'
    
    # Display configuration
    console.print("\n" + "="*60)
    console.print(f"[bold cyan]Cocotb Verification[/bold cyan]")
    if test:
        console.print(f"Test: [yellow]{test}[/yellow]")
    elif run_all:
        console.print(f"Running: [yellow]All tests[/yellow]")
    elif tag:
        console.print(f"Test list: [yellow]{tag}[/yellow]")
    console.print(f"Simulation: [yellow]{sim_arg}[/yellow]")
    console.print(f"PDK: [yellow]{pdk}[/yellow]")
    console.print("="*60 + "\n")
    
    if dry_run:
        console.print("[bold yellow]Dry run - configuration ready[/bold yellow]\n")
        openframe_flag = " --openframe" if project_type == 'openframe' else ""
        if test:
            console.print(f"Would run: {caravel_cocotb_bin} -t {test} -sim {sim_arg}{openframe_flag}")
        elif run_all:
            all_tests_yaml = cocotb_dir / ('all_tests_gl.yaml' if sim.lower() == 'gl' else 'all_tests.yaml')
            if all_tests_yaml.exists():
                yaml_path = all_tests_yaml.name
            else:
                yaml_file = 'user_proj_tests_gl.yaml' if sim.lower() == 'gl' else 'user_proj_tests.yaml'
                yaml_path = f'user_proj_tests/{yaml_file}'
            console.print(f"Would run: {caravel_cocotb_bin} -tl {yaml_path} -sim {sim_arg}{openframe_flag}")
        elif tag:
            console.print(f"Would run: {caravel_cocotb_bin} -tl {tag} -sim {sim_arg}{openframe_flag}")
        return
    
    # Prepare environment
    env = os.environ.copy()
    env['CARAVEL_ROOT'] = str(caravel_root)
    env['MCW_ROOT'] = str(mcw_root)
    env['PDK_ROOT'] = str(pdk_root)
    env['PDK'] = pdk
    env['PROJECT_ROOT'] = str(project_root_path)
    
    # Build command args
    cmd = [str(caravel_cocotb_bin)]
    
    if test:
        cmd.extend(['-t', test])
    elif run_all:
        # Look for test list yaml - prefer all_tests.yaml, fall back to user_proj_tests/
        all_tests_yaml = cocotb_dir / ('all_tests_gl.yaml' if sim.lower() == 'gl' else 'all_tests.yaml')
        if all_tests_yaml.exists():
            yaml_path = all_tests_yaml.name
        else:
            # Fall back to legacy user_proj_tests directory
            yaml_file = 'user_proj_tests_gl.yaml' if sim.lower() == 'gl' else 'user_proj_tests.yaml'
            yaml_path = f'user_proj_tests/{yaml_file}'
        cmd.extend(['-tl', yaml_path])
    elif tag:
        # User specified a custom test list
        # Check if tag is a directory or file path
        tag_path = cocotb_dir / tag
        if tag_path.is_dir():
            # If it's a directory, construct the YAML file path based on simulation type
            yaml_file = f'{tag}_gl.yaml' if sim.lower() == 'gl' else f'{tag}.yaml'
            yaml_path = f'{tag}/{yaml_file}'
            # Verify the file exists
            yaml_full_path = tag_path / yaml_file
            if not yaml_full_path.exists():
                console.print(f"[red]✗[/red] Test list file not found: {yaml_full_path}")
                console.print(f"[yellow]Expected: {yaml_path}[/yellow]")
                sys.exit(1)
            cmd.extend(['-tl', yaml_path])
        else:
            # It's already a file path, use it as-is
            cmd.extend(['-tl', tag])
    
    if sim.lower() == 'gl':
        cmd.extend(['-sim', 'GL'])
    
    # Add openframe flag for openframe projects
    if project_type == 'openframe':
        cmd.append('--openframe')
    
    # Add CI flag to disable Docker interactive mode (required when not running in a terminal)
    cmd.append('--CI')
    
    # Run cocotb tests
    console.print(f"[cyan]Running cocotb verification...[/cyan]")
    
    try:
        # Use Popen for better signal handling
        process = subprocess.Popen(
            cmd,
            cwd=str(cocotb_dir),
            env=env,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        # Wait for process to complete
        returncode = process.wait()
        
        if returncode == 0:
            console.print(f"\n[green]✓[/green] Verification passed!")
        elif returncode == -2 or returncode == 130:  # SIGINT
            console.print("\n[yellow]⚠[/yellow] Verification interrupted by user")
            sys.exit(130)
        else:
            console.print(f"\n[red]✗[/red] Verification failed with exit code {returncode}")
            console.print(f"[yellow]Check logs in: {cocotb_dir}[/yellow]")
            sys.exit(returncode)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Verification interrupted by user")
        # Try to stop the process group gracefully
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
            else:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
    except Exception as e:
        console.print(f"\n[red]✗[/red] Error: {e}")


DEFAULT_API_URL = 'https://api.chipfoundry.io'
PORTAL_BASE_URL = 'https://platform.chipfoundry.io'


def _get_api_url() -> str:
    config = load_user_config()
    return config.get('api_url', DEFAULT_API_URL)


def _get_portal_url() -> str:
    api_url = _get_api_url()
    if 'dev-api' in api_url:
        return 'https://dev-platform.chipfoundry.io'
    return PORTAL_BASE_URL


def _api_client():
    """Return an httpx client configured with API key auth. Returns (client, api_url) or raises SystemExit."""
    import httpx

    config = load_user_config()
    api_key = config.get('api_key')
    if not api_key:
        console.print("[yellow]Not logged in.[/yellow] Run [bold]cf login[/bold] to authenticate.")
        raise SystemExit(1)

    api_url = _get_api_url()
    client = httpx.Client(
        base_url=f"{api_url}/api/v1",
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=15,
    )
    return client, api_url


def _api_get(path: str):
    """Authenticated GET to the platform API. Returns parsed JSON or raises SystemExit."""
    client, _ = _api_client()
    try:
        resp = client.get(path)
        if resp.status_code == 401:
            console.print("[red]✗ API key is invalid or expired.[/red] Run [bold]cf login[/bold] to re-authenticate.")
            raise SystemExit(1)
        resp.raise_for_status()
        return resp.json()
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]✗ API request failed: {e}[/red]")
        raise SystemExit(1)
    finally:
        client.close()


def _api_post(path: str, json_data: dict):
    """Authenticated POST to the platform API. Returns parsed JSON or raises SystemExit."""
    client, _ = _api_client()
    try:
        resp = client.post(path, json=json_data)
        if resp.status_code == 401:
            console.print("[red]✗ API key is invalid or expired.[/red] Run [bold]cf login[/bold] to re-authenticate.")
            raise SystemExit(1)
        resp.raise_for_status()
        return resp.json()
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]✗ API request failed: {e}[/red]")
        raise SystemExit(1)
    finally:
        client.close()


def _api_put(path: str, json_data: dict):
    """Authenticated PUT to the platform API. Returns parsed JSON or raises SystemExit."""
    client, _ = _api_client()
    try:
        resp = client.put(path, json=json_data)
        if resp.status_code == 401:
            console.print("[red]✗ API key is invalid or expired.[/red] Run [bold]cf login[/bold] to re-authenticate.")
            raise SystemExit(1)
        resp.raise_for_status()
        return resp.json()
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]✗ API request failed: {e}[/red]")
        raise SystemExit(1)
    finally:
        client.close()


_SYNC_KEEP_KEYS = {"project", "tapeout"}


def _slim_project_json(pj: dict) -> dict:
    """Return a lightweight copy of project.json for API sync.

    The full file can exceed the 8 KB AWS WAF body-inspection limit when it
    contains DRC results, report summaries, etc.  The backend only needs the
    ``project`` and ``tapeout`` top-level sections; everything else is
    stripped to keep the payload small.
    """
    return {k: v for k, v in pj.items() if k in _SYNC_KEEP_KEYS}


def _load_project_platform_id(project_root: str):
    """Read platform_project_id from .cf/project.json, return None if absent."""
    pj = Path(project_root) / '.cf' / 'project.json'
    if not pj.exists():
        return None
    with open(pj) as f:
        data = json.load(f)
    return data.get('project', {}).get('platform_project_id')


def _save_platform_id(project_root: str, platform_id: str, project_name: str = None):
    """Write platform_project_id (and optionally project name) into .cf/project.json."""
    pj = Path(project_root) / '.cf' / 'project.json'
    with open(pj) as f:
        data = json.load(f)
    proj = data.setdefault('project', {})
    proj['platform_project_id'] = platform_id
    if project_name:
        old_name = proj.get('name')
        proj['name'] = project_name
        if old_name and old_name != project_name:
            console.print(f"[yellow]Updated project name: '{old_name}' → '{project_name}' (synced from platform)[/yellow]")
    with open(pj, 'w') as f:
        json.dump(data, f, indent=2)


@main.command('link')
@click.option('--id', 'project_id', default=None, help='Platform project UUID to link directly.')
@click.option('--name', 'project_name', default=None, help='Platform project name to search for.')
def link_cmd(project_id, project_name):
    """Link this local project to an existing platform project."""
    project_root = os.getcwd()
    pj_path = Path(project_root) / '.cf' / 'project.json'
    if not pj_path.exists():
        console.print("[yellow]No .cf/project.json found. Run [bold]cf init[/bold] first, or this command will create one.[/yellow]")
        create = console.input("Create a minimal project.json? (y/N): ").strip().lower()
        if create != 'y':
            return
        config = load_user_config()
        username = config.get("sftp_username", "unknown")
        pj_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"project": {"name": Path(project_root).name, "type": "", "user": username, "version": "1", "user_project_wrapper_hash": "", "submission_state": "Draft"}}
        with open(pj_path, 'w') as f:
            json.dump(data, f, indent=2)

    existing_id = _load_project_platform_id(project_root)
    if existing_id:
        overwrite = console.input(f"[yellow]Already linked to {existing_id}. Replace? (y/N): [/yellow]").strip().lower()
        if overwrite != 'y':
            return

    if project_id:
        project = _api_get(f"/projects/{project_id}")
        _save_platform_id(project_root, project['id'], project['name'])
        portal_url = _get_portal_url()
        console.print(f"[green]✓ Linked to {project['name']}[/green] ({project['id']})")
        console.print(f"  Portal: {portal_url}/projects/{project['id']}")
        return

    projects = _api_get("/projects/me")
    if not projects:
        console.print("[yellow]No projects found on the platform.[/yellow] Create one with [bold]cf init[/bold].")
        return

    if project_name:
        matches = [p for p in projects if project_name.lower() in p['name'].lower()]
        if not matches:
            console.print(f"[red]No projects matching '{project_name}' found.[/red]")
            return
        if len(matches) == 1:
            _save_platform_id(project_root, matches[0]['id'], matches[0]['name'])
            portal_url = _get_portal_url()
            console.print(f"[green]✓ Linked to {matches[0]['name']}[/green] ({matches[0]['id']})")
            console.print(f"  Portal: {portal_url}/projects/{matches[0]['id']}")
            return
        projects = matches

    console.print("\n[bold]Your platform projects:[/bold]")
    for i, p in enumerate(projects, 1):
        status_str = p.get('status', 'unknown')
        shuttle_str = f" — {p.get('shuttle_name', '')}" if p.get('shuttle_name') else ""
        console.print(f"  [cyan]{i}[/cyan]. {p['name']}{shuttle_str} [{status_str}]")

    choice = console.input("\nSelect project number: ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(projects):
            selected = projects[idx]
            _save_platform_id(project_root, selected['id'], selected['name'])
            portal_url = _get_portal_url()
            console.print(f"\n[green]✓ Linked to {selected['name']}[/green] ({selected['id']})")
            console.print(f"  Portal: {portal_url}/projects/{selected['id']}")
        else:
            console.print("[red]Invalid selection.[/red]")
    except ValueError:
        console.print("[red]Invalid selection.[/red]")


@main.command('unlink')
def unlink_cmd():
    """Remove the platform link from this project."""
    project_root = os.getcwd()
    platform_id = _load_project_platform_id(project_root)
    if not platform_id:
        console.print("[yellow]This project is not linked to the platform.[/yellow]")
        return

    confirm = console.input(f"Unlink from platform project {platform_id}? (y/N): ").strip().lower()
    if confirm != 'y':
        console.print("[dim]Cancelled.[/dim]")
        return

    pj_path = Path(project_root) / '.cf' / 'project.json'
    with open(pj_path) as f:
        data = json.load(f)
    data.get('project', {}).pop('platform_project_id', None)
    with open(pj_path, 'w') as f:
        json.dump(data, f, indent=2)
    console.print("[green]✓ Platform link removed.[/green] The remote project is not deleted.")


@main.command('login')
def login_cmd():
    """Authenticate with ChipFoundry platform via browser."""
    import httpx
    import webbrowser
    import time

    api_url = _get_api_url()
    console.print("[bold cyan]ChipFoundry CLI Login[/bold cyan]")
    console.print(f"Opening browser to authenticate with [bold]{api_url}[/bold]...\n")

    try:
        resp = httpx.post(f"{api_url}/api/v1/auth/cli/sessions", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        console.print(f"[red]✗ Failed to create login session: {e}[/red]")
        raise SystemExit(1)

    session_id = data['session_id']
    login_url = data['login_url']
    expires_at = data['expires_at']

    webbrowser.open(login_url)
    console.print(f"If the browser didn't open, visit this URL:\n[link={login_url}]{login_url}[/link]\n")
    console.print("Waiting for approval in browser...", style="dim")

    poll_url = f"{api_url}/api/v1/auth/cli/sessions/{session_id}"
    poll_interval = 2
    max_polls = 150  # 5 minutes at 2s intervals

    for _ in range(max_polls):
        time.sleep(poll_interval)
        try:
            poll_resp = httpx.get(poll_url, timeout=10)
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
        except httpx.HTTPError:
            continue

        status = poll_data.get('status')

        if status == 'completed':
            api_key = poll_data.get('api_key')
            user_email = poll_data.get('user_email', '')
            if not api_key:
                console.print("[red]✗ Session completed but no API key returned.[/red]")
                raise SystemExit(1)

            config = load_user_config()
            config['api_key'] = api_key
            if user_email:
                config['user_email'] = user_email
            save_user_config(config)

            console.print(f"\n[green]✓ Logged in as {user_email or 'authenticated user'}[/green]")
            console.print(f"  API key saved to {get_config_path()}")
            return

        if status == 'expired':
            console.print("\n[red]✗ Login session expired. Please try again.[/red]")
            raise SystemExit(1)

    console.print("\n[red]✗ Login timed out. Please try again.[/red]")
    raise SystemExit(1)


@main.command('logout')
def logout_cmd():
    """Remove stored API key and log out."""
    config = load_user_config()
    removed = False
    for key in ('api_key', 'user_email'):
        if key in config:
            del config[key]
            removed = True

    if removed:
        save_user_config(config)
        console.print("[green]✓ Logged out. API key removed.[/green]")
    else:
        console.print("[yellow]Not currently logged in.[/yellow]")


@main.command('whoami')
def whoami_cmd():
    """Show current authentication status."""
    import httpx

    config = load_user_config()
    api_key = config.get('api_key')

    if not api_key:
        console.print("[yellow]Not logged in.[/yellow] Run [bold]cf login[/bold] to authenticate.")
        raise SystemExit(1)

    api_url = _get_api_url()
    try:
        resp = httpx.get(
            f"{api_url}/api/v1/auth/cli/whoami",
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10,
        )
        if resp.status_code == 401:
            console.print("[red]✗ API key is invalid or expired.[/red] Run [bold]cf login[/bold] to re-authenticate.")
            raise SystemExit(1)
        resp.raise_for_status()
        user = resp.json()
        email = user.get('email', 'unknown')
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or email
        console.print(f"[green]✓ Logged in as {name}[/green] ({email})")
    except httpx.HTTPError as e:
        console.print(f"[red]✗ Could not verify credentials: {e}[/red]")
        stored_email = config.get('user_email')
        if stored_email:
            console.print(f"  Last known user: {stored_email}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()