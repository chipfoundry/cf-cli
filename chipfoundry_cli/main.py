import click
import getpass
from chipfoundry_cli.utils import (
    collect_project_files, ensure_cf_directory, update_or_create_project_json,
    sftp_connect, upload_with_progress, sftp_ensure_dirs, sftp_download_recursive,
    get_config_path, load_user_config, save_user_config, GDS_TYPE_MAP,
    open_html_in_browser, download_with_progress
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

DEFAULT_SSH_KEY = os.path.expanduser('~/.ssh/chipfoundry-key')
DEFAULT_SFTP_HOST = 'sftp.chipfoundry.io'

console = Console()

def get_project_json_from_cwd():
    cf_path = Path(os.getcwd()) / '.cf' / 'project.json'
    if cf_path.exists():
        with open(cf_path) as f:
            data = json.load(f)
        project_name = data.get('project', {}).get('name')
        return str(Path(os.getcwd())), project_name
    return None, None

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
    version = console.input("Version (default 1.0.0): ").strip() or "1.0.0"
    # No hash yet, will be filled by push
    data = {
        "project": {
            "name": name,
            "type": project_type,
            "user": username,
            "version": version,
            "user_project_wrapper_hash": ""
        }
    }
    with open(project_json_path, 'w') as f:
        json.dump(data, f, indent=2)
    console.print(f"[green]Initialized project at {project_json_path}[/green]")

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

if __name__ == "__main__":
    main() 