import click
import getpass
from chipfoundry_cli.utils import (
    collect_project_files, ensure_cf_directory, update_or_create_project_json,
    sftp_connect, upload_with_progress, sftp_ensure_dirs,
    get_config_path, load_user_config, save_user_config
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

GDS_TYPE_MAP = {
    'user_project_wrapper.gds': 'digital',
    'user_analog_project_wrapper.gds': 'analog',
    'openframe_project_wrapper.gds': 'openframe',
}

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
    gds_type_map = {
        'user_project_wrapper.gds': 'digital',
        'user_analog_project_wrapper.gds': 'analog',
        'openframe_project_wrapper.gds': 'openframe',
    }
    for gds_name, gtype in gds_type_map.items():
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
    
    # Use the detected GDS file for upload and hash
    if gds_file_path:
        collected['gds/user_project_wrapper.gds'] = gds_file_path
    
    # Prepare CLI overrides for project.json
    cli_overrides = {
        "project_id": project_id,
        "project_name": project_name,
        "project_type": detected_type,
        "sftp_username": sftp_username,
    }
    cf_dir = ensure_cf_directory(project_root)
    project_json_path = update_or_create_project_json(
        cf_dir=str(cf_dir),
        gds_path=collected["gds/user_project_wrapper.gds"],
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
        "gds/user_project_wrapper.gds": collected["gds/user_project_wrapper.gds"],
        "verilog/rtl/user_defines.v": collected["verilog/rtl/user_defines.v"],
    }
    
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
    config = load_user_config()
    if not sftp_username:
        sftp_username = config.get("sftp_username")
        if not sftp_username:
            console.print("[bold red]No SFTP username provided and not found in config. Please run 'chipfoundry config' or provide --sftp-username.[/bold red]")
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
        remote_dir = f"outgoing/results/{project_name}"
        output_dir = os.path.join(os.getcwd(), "sftp-output", project_name)
        os.makedirs(output_dir, exist_ok=True)
        try:
            files = sftp.listdir(remote_dir)
        except Exception:
            console.print(f"[yellow]No results found for project '{project_name}' on SFTP server.[/yellow]")
            return
        if not files:
            console.print(f"[yellow]No files to download for project '{project_name}'.[/yellow]")
            return
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total} bytes"),
            TimeElapsedColumn(),
        ) as progress:
            for fname in files:
                remote_path = f"{remote_dir}/{fname}"
                local_path = os.path.join(output_dir, fname)
                try:
                    file_size = sftp.stat(remote_path).st_size
                    task = progress.add_task(f"Downloading {fname}", total=file_size)
                    with open(local_path, "wb") as f:
                        def callback(bytes_transferred, total=file_size):
                            progress.update(task, completed=bytes_transferred)
                        sftp.getfo(remote_path, f, callback=callback)
                    progress.update(task, completed=file_size)
                except Exception as e:
                    console.print(f"[red]Failed to download {fname}: {e}[/red]")
        console.print(f"[green]All files downloaded to {output_dir}[/green]")
    finally:
        if transport:
            sftp.close()
            transport.close()

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
        # List projects in incoming/projects/ and outgoing/results/
        incoming_projects_dir = f"incoming/projects"
        outgoing_results_dir = f"outgoing/results"
        projects = []
        results = []
        try:
            projects = sftp.listdir(incoming_projects_dir)
        except Exception:
            pass
        try:
            results = sftp.listdir(outgoing_results_dir)
        except Exception:
            pass
        table = Table(title=f"SFTP Status for {sftp_username}")
        table.add_column("Project Name", style="cyan", no_wrap=True)
        table.add_column("Has Input", style="yellow")
        table.add_column("Has Output", style="green")
        all_projects = set(projects) | set(results)
        for proj in sorted(all_projects):
            has_input = "Yes" if proj in projects else "No"
            has_output = "Yes" if proj in results else "No"
            table.add_row(proj, has_input, has_output)
        if all_projects:
            console.print(table)
        else:
            console.print("[yellow]No projects or results found on SFTP server.[/yellow]")
    finally:
        if transport:
            sftp.close()
            transport.close()

if __name__ == "__main__":
    main() 