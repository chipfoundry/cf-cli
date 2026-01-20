import click
import getpass
from chipfoundry_cli.utils import (
    collect_project_files, ensure_cf_directory, update_or_create_project_json,
    sftp_connect, upload_with_progress, sftp_ensure_dirs, sftp_download_recursive,
    get_config_path, load_user_config, save_user_config, GDS_TYPE_MAP,
    open_html_in_browser, download_with_progress, update_repo_files,
    fetch_versions_from_upstream, parse_user_defines_v, update_user_defines_v,
    get_gpio_config_from_project_json, save_gpio_config_to_project_json,
    GPIO_MODES, GPIO_MODE_DESCRIPTIONS
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

def check_project_initialized(project_root_path: Path, command_name: str):
    """
    Check if project is initialized (has .cf/project.json).
    Raises click.Abort with helpful message if not initialized.
    """
    project_json_path = project_root_path / '.cf' / 'project.json'
    if not project_json_path.exists():
        console.print(f"[red]✗ Project not initialized. Please run 'cf init' first.[/red]")
        raise click.Abort()

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
def init(project_root):
    """Initialize a new ChipFoundry project (.cf/project.json) in the given directory."""
    if not project_root:
        project_root = os.getcwd()
    cf_dir = Path(project_root) / '.cf'
    cf_dir.mkdir(parents=True, exist_ok=True)
    project_json_path = cf_dir / 'project.json'
    if project_json_path.exists():
        overwrite = console.input(f"[yellow]project.json already exists at {project_json_path}. Overwrite? (y/N): [/yellow]").strip().lower()
        if overwrite != 'y':
            console.print("[red]Aborted project initialization.[/red]")
            return
    # Get username from user config
    config = load_user_config()
    username = config.get("sftp_username")
    if not username:
        console.print("[bold red]No SFTP username found in user config. Please run 'chipfoundry config' first.[/bold red]")
        raise click.Abort()
    # Auto-detect project type from GDS file name
    gds_dir = Path(project_root) / 'gds'
    gds_type = None
    for gds_name, gtype in GDS_TYPE_MAP.items():
        if (gds_dir / gds_name).exists():
            gds_type = gtype
            break
    
    # Default project name to directory name
    default_name = Path(project_root).name
    
    name = console.input(f"Project name (detected: [cyan]{default_name}[/cyan]): ").strip() or default_name
    
    # Suggest project type if detected
    if gds_type:
        project_type = console.input(f"Project type (digital/analog/openframe) (detected: [cyan]{gds_type}[/cyan]): ").strip() or gds_type
    else:
        project_type = console.input("Project type (digital/analog/openframe): ").strip()
    version = "1"  # Start with version 1, will be auto-incremented on push
    # No hash yet, will be filled by push
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
    with open(project_json_path, 'w') as f:
        json.dump(data, f, indent=2)
    console.print(f"[green]Initialized project at {project_json_path}[/green]")

@main.command('gpio-config')
@click.option('--project-root', required=False, type=click.Path(exists=True, file_okay=False), help='Path to the project directory (defaults to current directory).')
def gpio_config(project_root):
    """Configure GPIO settings interactively and save to project config and user_defines.v."""
    if not project_root:
        project_root = os.getcwd()
    
    project_root = Path(project_root)
    
    # Check if project is initialized
    check_project_initialized(project_root, 'gpio-config')
    
    project_json_path = project_root / '.cf' / 'project.json'
    
    # Load project type from project.json
    with open(project_json_path, 'r') as f:
        project_data = json.load(f)
    project_type = project_data.get('project', {}).get('type', 'digital')
    
    # For openframe, GPIO config is not needed
    if project_type == 'openframe':
        console.print("[red]✗ GPIO configuration is not available for openframe projects.[/red]")
        console.print("[yellow]Openframe projects do not use user_defines.v.[/yellow]")
        raise click.Abort()
    
    user_defines_path = project_root / 'verilog' / 'rtl' / 'user_defines.v'
    
    # Load existing GPIO configs from project.json or user_defines.v
    existing_configs = get_gpio_config_from_project_json(str(project_json_path))
    if not existing_configs and user_defines_path.exists():
        # Try to parse from user_defines.v
        existing_configs = parse_user_defines_v(str(user_defines_path))
    
    # Determine GPIO range based on project type
    if project_type == 'analog':  # caravan
        # Caravan: GPIO 5-13 and 25-37 (GPIO 14-24 not available)
        # User sees: 5-13, then 14-26 (which map to 25-37 internally)
        available_gpios = list(range(5, 14)) + list(range(25, 38))
        user_to_real_map = {}
        user_num = 5
        for real_gpio in available_gpios:
            user_to_real_map[user_num] = real_gpio
            user_num += 1
        real_to_user_map = {v: k for k, v in user_to_real_map.items()}
        console.print("\n[bold cyan]GPIO Configuration (Caravan)[/bold cyan]")
        console.print("Configure GPIO pins 5-13, then 14-26 (GPIO 0-4 are fixed system pins)\n")
        console.print("[dim]Note: GPIO 14-24 are not available in Caravan. Numbers 14-26 map to GPIO 25-37.[/dim]\n")
    else:  # digital (caravel)
        # Caravel: GPIO 5-37 all available
        available_gpios = list(range(5, 38))
        user_to_real_map = {gpio: gpio for gpio in available_gpios}
        real_to_user_map = {gpio: gpio for gpio in available_gpios}
        console.print("\n[bold cyan]GPIO Configuration (Caravel)[/bold cyan]")
        console.print("Configure GPIO pins 5-37 (GPIO 0-4 are fixed system pins)\n")
    
    # Create a list of GPIO mode options for selection, excluding "invalid"
    mode_options = [key for key in GPIO_MODES.keys() if key != "invalid"]
    
    # Show modes in a more compact table format
    table = Table(title="Available GPIO Modes", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Key", style="cyan", width=20)
    table.add_column("Description", style="white")
    
    for i, key in enumerate(mode_options, 1):
        table.add_row(str(i), key, GPIO_MODE_DESCRIPTIONS[key])
    
    console.print(table)
    console.print("[dim]Tip: Enter number or full key. [/dim]\n")
    
    gpio_configs = {}
    
    # Helper function to find mode key from mode name or hex value
    def find_mode_key(mode_value):
        """Find the mode key for a given mode value."""
        if not mode_value:
            return None
        # Direct match
        for key, mode_name in GPIO_MODES.items():
            if mode_name == mode_value:
                # Don't return "invalid" if it's not in our selectable options
                if key != "invalid":
                    return key
        # Check if it's a hex value or invalid - return None to indicate invalid
        if mode_value.startswith('0x') or mode_value.startswith("13'h") or 'INVALID' in mode_value:
            return None  # Don't show "invalid", just show no default
        return None
    
    # Helper function to check if a mode is invalid
    def is_invalid_mode(mode_value):
        """Check if a mode value is invalid."""
        if not mode_value:
            return True
        if mode_value == GPIO_MODES.get("invalid"):
            return True
        if mode_value.startswith('0x') or mode_value.startswith("13'h") or 'INVALID' in mode_value:
            return True
        return False
    
    # Helper function to find matching mode by partial input
    def find_matching_mode(user_input):
        """Find mode that matches user input (partial match or full key)."""
        user_input_lower = user_input.lower()
        
        # Check for exact key match
        if user_input in mode_options:
            return user_input
        
        # Check for partial matches (case-insensitive)
        matches = [key for key in mode_options if key.lower().startswith(user_input_lower)]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            # Multiple matches - return list so we can show them
            return matches
        
        return None
    
    # Configure each available GPIO
    for user_gpio_num in sorted(user_to_real_map.keys()):
        real_gpio_num = user_to_real_map[user_gpio_num]
        
        # Get current value if exists (using real GPIO number)
        current_mode = existing_configs.get(real_gpio_num) if existing_configs else None
        detected_key = find_mode_key(current_mode) if current_mode else None
        is_invalid = is_invalid_mode(current_mode)
        
        # Show GPIO number with mapping info for caravan
        gpio_display = f"GPIO {user_gpio_num}"
        if project_type == 'analog' and user_gpio_num >= 14:
            # Show the real GPIO number for caravan
            gpio_display = f"GPIO {user_gpio_num} (GPIO {real_gpio_num})"
        
        # Build prompt with detected default (only show if valid)
        if detected_key and not is_invalid:
            prompt_text = f"{gpio_display} ([cyan]{detected_key}[/cyan]): "
        else:
            prompt_text = f"{gpio_display}: "
        
        while True:
            user_input = console.input(prompt_text).strip()
            
            if not user_input:
                # If current mode is invalid, require input
                if is_invalid_mode(current_mode):
                    console.print(f"[red]{gpio_display} is currently invalid. Please enter a valid mode (1-{len(mode_options)} or mode key).[/red]")
                    continue
                # Use current value if user just presses enter and we have a valid one
                if current_mode:
                    gpio_configs[real_gpio_num] = current_mode
                    break
                else:
                    # No current value and no input - require input
                    console.print(f"[red]{gpio_display} has no configuration. Please enter a valid mode (1-{len(mode_options)} or mode key).[/red]")
                    continue
            elif user_input.isdigit():
                # User selected by number
                choice_num = int(user_input)
                if 1 <= choice_num <= len(mode_options):
                    selected_key = mode_options[choice_num - 1]
                    gpio_configs[real_gpio_num] = GPIO_MODES[selected_key]
                    break
                else:
                    console.print(f"[red]Invalid choice. Please enter 1-{len(mode_options)}.[/red]")
            else:
                # Try to find matching mode
                match_result = find_matching_mode(user_input)
                
                if match_result is None:
                    console.print(f"[red]No match found for '{user_input}'. Try a number (1-{len(mode_options)}), partial key, or full key.[/red]")
                elif isinstance(match_result, list):
                    # Multiple matches found
                    console.print(f"[yellow]Multiple matches found: {', '.join(match_result)}[/yellow]")
                    console.print(f"[dim]Please be more specific or use a number (1-{len(mode_options)}).[/dim]")
                else:
                    # Single match found
                    gpio_configs[real_gpio_num] = GPIO_MODES[match_result]
                    break
    
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
            # Look for caravel directory in common locations
            caravel_paths = [
                project_root / 'caravel',
                project_root / 'dependencies' / 'caravel',
                project_root.parent / 'caravel',  # If caravel is sibling to project
            ]
            
            gen_gpio_script = None
            for caravel_path in caravel_paths:
                script_path = caravel_path / 'scripts' / 'gen_gpio_defaults.py'
                if script_path.exists():
                    gen_gpio_script = script_path
                    break
            
            if gen_gpio_script:
                try:
                    console.print("[cyan]Generating GPIO defaults for simulation...[/cyan]")
                    result = subprocess.run(
                        [sys.executable, str(gen_gpio_script)],
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    console.print(f"[green]✓ Generated GPIO defaults[/green]")
                except subprocess.CalledProcessError as e:
                    console.print(f"[yellow]Warning: Failed to run gen_gpio_defaults.py: {e}[/yellow]")
                    if e.stderr:
                        console.print(f"[dim]{e.stderr}[/dim]")
                except Exception as e:
                    console.print(f"[yellow]Warning: Error running gen_gpio_defaults.py: {e}[/yellow]")
            else:
                console.print("[dim]Note: gen_gpio_defaults.py not found. Caravel may not be installed yet.[/dim]")
                console.print("[dim]Run 'cf setup' to install Caravel, or run the script manually after setup.[/dim]")
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
def push(project_root, sftp_host, sftp_username, sftp_key, project_id, project_name, project_type, force_overwrite, dry_run):
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
    # Load user config for defaults
    config = load_user_config()
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
        "verilog/rtl/user_defines.v": collected["verilog/rtl/user_defines.v"],
    }
    
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
            
            # Automatically update local project config if available
            pulled_config_path = os.path.join(output_dir, "config", "project.json")
            if os.path.exists(pulled_config_path):
                local_config_path = os.path.join(".cf", "project.json")
                os.makedirs(".cf", exist_ok=True)
                
                try:
                    import shutil
                    shutil.copy2(pulled_config_path, local_config_path)
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

@main.command('status')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=False, help='SFTP username (defaults to config).')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file (defaults to config).', default=None, show_default=False)
def status(sftp_host, sftp_username, sftp_key):
    """Show all projects and outputs for the user on the SFTP server."""
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
    
    # Check if project is initialized
    check_project_initialized(project_root_path, 'setup')
    
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
    install_mcw = only_mcw or not only_mode
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
        f"Caravel variant: [yellow]{'caravel-lite' if caravel_lite else 'caravel'}[/yellow]",
    ]
    
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
    if install_mcw:
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
        caravel_venv_dir = project_root_path / 'caravel' / 'venv'
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
            check_python_package_installed(caravel_venv_dir, 'ciel') and
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
                # Check if caravel directory exists
                caravel_dir = project_root_path / 'caravel'
                if not caravel_dir.exists():
                    console.print("[yellow]Warning: Caravel not found. Install caravel first.[/yellow]")
                    console.print("[cyan]Run: cf setup --only-caravel[/cyan]")
                else:
                    # Remove existing venv if overwriting or doesn't exist
                    if caravel_venv_dir.exists() and (overwrite or not is_installed):
                        console.print("[cyan]Removing existing Ciel venv...[/cyan]")
                        shutil.rmtree(caravel_venv_dir)
                    
                    if not caravel_venv_dir.exists():
                        console.print("[cyan]Creating Ciel virtual environment...[/cyan]")
                        subprocess.run(
                            [sys.executable, '-m', 'venv', str(caravel_venv_dir)],
                            check=True,
                            capture_output=True
                        )
                        
                        venv_python = str(caravel_venv_dir / 'bin' / 'python3')
                        
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
                        
                        ciel_bin = str(caravel_venv_dir / 'bin' / 'ciel')
                        
                        # Set up environment with PDK_ROOT
                        env = os.environ.copy()
                        env['PDK_ROOT'] = str(pdk_root)
                        env['CIEL_DATA_SOURCE'] = 'static-web:https://chipfoundry.github.io/ciel-releases'
                        
                        result = subprocess.run(
                            [ciel_bin, 'enable', '--pdk-family', pdk_family, open_pdks_commit],
                            cwd=str(caravel_dir),
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
        
        # Check if already installed
        is_installed = check_python_package_installed(venv_cocotb, 'caravel-cocotb')
        
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
                    
                    console.print("[cyan]Installing caravel-cocotb...[/cyan]")
                    subprocess.run(
                        [venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', 'pip'],
                        check=True,
                        capture_output=True
                    )
                    subprocess.run(
                        [venv_python, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', 'caravel-cocotb'],
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
        precheck_dir = Path.home() / 'mpw_precheck'
        
        # Check if already installed
        is_installed = precheck_dir.exists() and (precheck_dir / '.git').exists()
        
        if is_installed and not overwrite:
            console.print("[green]✓[/green] Precheck already installed")
        elif dry_run:
            if is_installed:
                console.print("[dim]Would reinstall mpw_precheck [--overwrite][/dim]")
            else:
                console.print("[dim]Would install mpw_precheck[/dim]")
        else:
            try:
                if precheck_dir.exists() and overwrite:
                    console.print(f"[cyan]Removing existing {precheck_dir}...[/cyan]")
                    shutil.rmtree(precheck_dir)
                
                if not precheck_dir.exists():
                    console.print("[cyan]Cloning mpw_precheck...[/cyan]")
                    subprocess.run(
                        ['git', 'clone', '--depth=1', 'https://github.com/chipfoundry/mpw_precheck.git', str(precheck_dir)],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    console.print("[green]✓[/green] Precheck cloned successfully")
                
                console.print("[cyan]Pulling precheck Docker image...[/cyan]")
                subprocess.run(
                    ['docker', 'pull', 'chipfoundry/mpw_precheck:latest'],
                    check=True,
                    capture_output=True
                )
                console.print("[green]✓[/green] Precheck Docker image ready")
                
            except subprocess.CalledProcessError as e:
                maybe_abort_no_space(e, "Precheck install")
                had_errors = True
                console.print(f"[red]✗[/red] Failed to install precheck: {e}")
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
        cf harden user_proj_example
        cf harden user_project_wrapper
        cf harden --list
    """
    from datetime import datetime
    
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    # Check if project is initialized (skip check for --list)
    if not list_designs:
        check_project_initialized(project_root_path, 'harden')
    
    openlane_dir = project_root_path / 'openlane'
    
    # Check if openlane directory exists
    if not openlane_dir.exists():
        console.print(f"[red]✗[/red] OpenLane directory not found: {openlane_dir}")
        console.print("[yellow]Run 'cf setup' first to install OpenLane[/yellow]")
        return
    
    # List designs if requested
    if list_designs:
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
    
    # Macro is required if not listing
    if not macro:
        console.print("[red]✗[/red] Error: MACRO argument is required")
        console.print("[yellow]Usage:[/yellow] cf harden <macro>")
        console.print("[yellow]       [/yellow] cf harden --list")
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
        sys.exit(1)
    
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
            sys.exit(1)
    
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
            sys.exit(1)
    
    # Error if neither is available
    if not use_nix and not use_docker:
        console.print("[red]✗[/red] Neither Nix nor Docker is available")
        console.print("\n[yellow]LibreLane requires either:[/yellow]")
        console.print("  1. [cyan]Nix[/cyan] - Install from: https://librelane.readthedocs.io")
        console.print("  2. [cyan]Docker[/cyan] - Install from: https://docker.com")
        console.print("\nAfter installing either one, try again.")
        sys.exit(1)
    
    execution_method = "Nix" if use_nix else "Docker"
    
    # Set up environment variables
    caravel_root = project_root_path / 'caravel'
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
            'CARAVEL_ROOT': str(caravel_root),
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
            'CARAVEL_ROOT': str(caravel_root),
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
            '-m', str(caravel_root),
            '--dockerized',
        ]
        
        # Add --docker-no-tty if not running in a TTY (e.g., CI environments)
        try:
            import sys
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
@click.option('--disable-lvs', is_flag=True, help='Disable LVS check and run specific checks only')
@click.option('--checks', multiple=True, help='Specific checks to run (can be specified multiple times)')
@click.option('--dry-run', is_flag=True, help='Show the command without running')
def precheck(project_root, disable_lvs, checks, dry_run):
    """Run mpw_precheck validation on the project.
    
    This runs the MPW (Multi-Project Wafer) precheck tool to validate
    your design before submission.
    
    Examples:
        cf precheck                     # Run all checks
        cf precheck --disable-lvs       # Skip LVS, run specific checks
        cf precheck --checks license --checks makefile  # Run specific checks
    """
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    # Check if project is initialized
    check_project_initialized(project_root_path, 'precheck')
    
    project_json_path = project_root_path / '.cf' / 'project.json'
    
    # Check project type - GPIO config not needed for openframe
    with open(project_json_path, 'r') as f:
        project_data = json.load(f)
    project_type = project_data.get('project', {}).get('type', 'digital')
    
    # Check if GPIO configuration exists (not needed for openframe)
    if project_type != 'openframe':
        gpio_config = get_gpio_config_from_project_json(str(project_json_path))
        if not gpio_config or len(gpio_config) == 0:
            console.print("[red]✗[/red] GPIO configuration not found in project.json")
            console.print("[yellow]GPIO configuration is required before running precheck.[/yellow]")
            console.print("[cyan]Please run 'cf gpio-config' to configure GPIO settings first.[/cyan]")
            raise click.Abort()
    
    precheck_root = Path.home() / 'mpw_precheck'
    pdk_root = project_root_path / 'dependencies' / 'pdks'
    
    # Detect PDK from project.json
    pdk = 'sky130A'
    if project_json_path.exists():
        try:
            with open(project_json_path, 'r') as f:
                project_data = json.load(f)
                pdk = project_data.get('pdk', 'sky130A')
        except:
            pass
    
    # Check if precheck is installed
    if not precheck_root.exists():
        console.print(f"[red]✗[/red] mpw_precheck not found at {precheck_root}")
        console.print("[yellow]Run 'cf setup --only-precheck' to install[/yellow]")
        return
    
    # Check if PDK exists
    if not (pdk_root / pdk).exists():
        console.print(f"[red]✗[/red] PDK not found at {pdk_root / pdk}")
        console.print("[yellow]Run 'cf setup --only-pdk' to install[/yellow]")
        return
    
    # Check Docker availability
    docker_available = shutil.which('docker') is not None
    if not docker_available:
        console.print("[red]✗[/red] Docker not found. Docker is required to run precheck.")
        return
    
    # Build the checks list
    if checks:
        # User specified custom checks
        checks_list = list(checks)
    elif disable_lvs:
        # Default checks when LVS is disabled
        checks_list = [
            'license', 'makefile', 'default', 'documentation', 'consistency',
            'gpio_defines', 'xor', 'magic_drc', 'klayout_feol', 'klayout_beol',
            'klayout_offgrid', 'klayout_met_min_ca_density',
            'klayout_pin_label_purposes_overlapping_drawing', 'klayout_zeroarea'
        ]
    else:
        # All checks (default behavior)
        checks_list = []
    
    # Display configuration
    console.print("\n" + "="*60)
    console.print("[bold cyan]MPW Precheck[/bold cyan]")
    console.print(f"Project: [yellow]{project_root_path}[/yellow]")
    console.print(f"PDK: [yellow]{pdk}[/yellow]")
    if disable_lvs:
        console.print("Mode: [yellow]LVS disabled[/yellow]")
    if checks_list:
        console.print(f"Checks: [yellow]{', '.join(checks_list)}[/yellow]")
    else:
        console.print("Checks: [yellow]All checks[/yellow]")
    console.print("="*60 + "\n")
    
    # Build Docker command
    import getpass
    import pwd
    
    user_id = os.getuid()
    group_id = os.getgid()
    
    pdk_path = pdk_root / pdk
    pdkpath = pdk_path  # Same as PDK_PATH in the Makefile
    ipm_dir = Path.home() / '.ipm'
    
    # Create .ipm directory if it doesn't exist
    if not ipm_dir.exists():
        ipm_dir.mkdir(parents=True, exist_ok=True)
    
    docker_cmd = [
        'docker', 'run', '--rm',
        '-v', f'{precheck_root}:{precheck_root}',
        '-v', f'{project_root_path}:{project_root_path}',
        '-v', f'{pdk_root}:{pdk_root}',
        '-v', f'{ipm_dir}:{ipm_dir}',
        '-e', f'INPUT_DIRECTORY={project_root_path}',
        '-e', f'PDK_PATH={pdk_path}',
        '-e', f'PDK_ROOT={pdk_root}',
        '-e', f'PDKPATH={pdkpath}',
        '-u', f'{user_id}:{group_id}',
        'chipfoundry/mpw_precheck:latest',
        'bash', '-c',
    ]
    
    # Build the precheck command
    precheck_cmd = f'cd {precheck_root} ; python3 mpw_precheck.py --input_directory {project_root_path} --pdk_path {pdk_path}'
    
    if checks_list:
        precheck_cmd += ' ' + ' '.join(checks_list)
    
    docker_cmd.append(precheck_cmd)
    
    if dry_run:
        console.print("[bold yellow]Dry run - would execute:[/bold yellow]\n")
        console.print("[dim]" + ' '.join(docker_cmd) + "[/dim]")
        return
    
    # Run precheck
    console.print("[cyan]Running mpw_precheck...[/cyan]")
    
    try:
        # Use Popen for better signal handling
        process = subprocess.Popen(
            docker_cmd,
            cwd=str(precheck_root),
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        # Wait for process to complete
        returncode = process.wait()
        
        console.print("")  # Add newline
        if returncode == 0:
            console.print("[green]✓[/green] Precheck passed!")
        elif returncode == -2 or returncode == 130:  # SIGINT
            console.print("[yellow]⚠[/yellow] Precheck interrupted by user")
            sys.exit(130)
        else:
            console.print(f"[red]✗[/red] Precheck failed with exit code {returncode}")
            console.print(f"[yellow]Check the output above for details[/yellow]")
            sys.exit(returncode)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠[/yellow] Precheck interrupted by user")
        # Try to stop the Docker container gracefully
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
@click.option('--tag', help='Test list tag/yaml file (e.g., user_proj_tests)')
@click.option('--dry-run', is_flag=True, help='Show the configuration without running')
def verify(test, project_root, sim, list_tests, run_all, tag, dry_run):
    """Run cocotb verification tests.
    
    Examples:
        cf verify --list                    # List all available tests
        cf verify counter_la                # Run a specific test (RTL)
        cf verify counter_la --sim gl       # Run gate-level simulation
        cf verify --all                     # Run all tests
        cf verify --tag user_proj_tests     # Run tests from a yaml list
    """
    # If .cf/project.json exists in cwd, use it as default project_root
    cwd_root, _ = get_project_json_from_cwd()
    if not project_root and cwd_root:
        project_root = cwd_root
    if not project_root:
        project_root = os.getcwd()
    
    project_root_path = Path(project_root)
    
    # Check if project is initialized (skip check if just listing tests)
    if not list_tests:
        check_project_initialized(project_root_path, 'verify')
    
    project_json_path = project_root_path / '.cf' / 'project.json'
    
    # Check if GPIO configuration exists (skip check if just listing tests or openframe)
    if not list_tests:
        # Check project type - GPIO config not needed for openframe
        with open(project_json_path, 'r') as f:
            project_data = json.load(f)
        project_type = project_data.get('project', {}).get('type', 'digital')
        
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
        if test:
            console.print(f"Would run: {caravel_cocotb_bin} -t {test} -sim {sim_arg}")
        elif run_all:
            yaml_file = 'user_proj_tests_gl.yaml' if sim.lower() == 'gl' else 'user_proj_tests.yaml'
            console.print(f"Would run: {caravel_cocotb_bin} -tl user_proj_tests/{yaml_file} -sim {sim_arg}")
        elif tag:
            console.print(f"Would run: {caravel_cocotb_bin} -tl {tag} -sim {sim_arg}")
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
        # Use the appropriate test list yaml
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


if __name__ == "__main__":
    main() 