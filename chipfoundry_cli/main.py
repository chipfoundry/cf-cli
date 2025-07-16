import click
import getpass
from chipfoundry_cli.utils import (
    collect_project_files, ensure_cf_directory, update_or_create_project_json,
    sftp_connect, upload_with_progress, sftp_ensure_dirs
)
import os
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

DEFAULT_SSH_KEY = os.path.expanduser('~/.ssh/id_rsa')
DEFAULT_SFTP_HOST = 'sftp.chipfoundry.io'

GDS_TYPE_MAP = {
    'user_project_wrapper.gds': 'digital',
    'user_analog_project_wrapper.gds': 'analog',
    'openframe_project_wrapper.gds': 'openframe',
}

console = Console()

@click.group(help="ChipFoundry CLI: Automate project submission and management.")
def main():
    pass

@main.command('submit')
@click.option('--project-root', required=True, type=click.Path(exists=True, file_okay=False), help='Path to the local ChipFoundry project directory.')
@click.option('--sftp-host', default=DEFAULT_SFTP_HOST, show_default=True, help='SFTP server hostname.')
@click.option('--sftp-username', required=True, help='SFTP username.')
@click.option('--sftp-key', type=click.Path(exists=True, dir_okay=False), help='Path to SFTP private key file. Defaults to ~/.ssh/id_rsa if it exists.', default=None, show_default=False)
@click.option('--sftp-password', help='SFTP password. If not provided, will prompt securely.', default=None)
@click.option('--project-id', help='Project ID (e.g., "user123_proj456"). Overrides project.json if exists.')
@click.option('--project-name', help='Project name (e.g., "my_project"). Overrides project.json if exists.')
@click.option('--project-type', help='Project type (auto-detected if not provided).', default=None)
@click.option('--force-overwrite', is_flag=True, help='Overwrite existing files on SFTP without prompting.')
@click.option('--dry-run', is_flag=True, help='Preview actions without uploading files.')
def submit(project_root, sftp_host, sftp_username, sftp_key, sftp_password, project_id, project_name, project_type, force_overwrite, dry_run):
    """Submit a project to the SFTP server."""
    # Determine which authentication method to use
    key_path = sftp_key
    password = sftp_password
    # If neither provided, try default key
    if not key_path and not password:
        if os.path.exists(DEFAULT_SSH_KEY):
            key_path = DEFAULT_SSH_KEY
            console.print(f"[INFO] Using default SSH key: {DEFAULT_SSH_KEY}", style="bold cyan")
        else:
            console.print("[WARN] No SFTP key or password provided, and no default key found at ~/.ssh/id_rsa.", style="bold yellow")
            auth_method = click.prompt("Choose authentication method (key/password)", type=click.Choice(['key', 'password']), show_choices=True)
            if auth_method == 'key':
                key_path = click.prompt("Enter path to SFTP private key", type=click.Path(exists=True, dir_okay=False))
            else:
                password = click.prompt("SFTP Password", hide_input=True)
    elif key_path and password:
        console.print("[ERROR] Options --sftp-password and --sftp-key are mutually exclusive.", style="bold red")
        raise click.UsageError("Options --sftp-password and --sftp-key are mutually exclusive.")
    elif not key_path and password:
        pass  # password provided
    elif key_path and not password:
        if not os.path.exists(key_path):
            console.print(f"[ERROR] SFTP key file not found: {key_path}", style="bold red")
            raise click.UsageError(f"SFTP key file not found: {key_path}")

    console.print(f"[INFO] Collecting project files from: {project_root}", style="bold cyan")
    try:
        collected = collect_project_files(project_root)
        for rel_path, abs_path in collected.items():
            if abs_path:
                console.print(f"[OK] Found: {rel_path} -> {abs_path}", style="green")
            else:
                console.print(f"[INFO] Optional file not found: {rel_path}", style="yellow")
    except FileNotFoundError as e:
        console.print(f"[ERROR] {e}", style="bold red")
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
            console.print("[ERROR] No recognized GDS file found for project type detection.", style="bold red")
            raise click.Abort()
        elif len(found_types) > 1:
            console.print(f"[ERROR] Multiple GDS types found: {found_types}. Only one project type is allowed per project.", style="bold red")
            raise click.Abort()
        else:
            detected_type = found_types[0]
            console.print(f"[INFO] Detected project type: {detected_type}", style="bold cyan")
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
    console.print(f"[INFO] Generating/updating project.json in {cf_dir}", style="bold cyan")
    project_json_path = update_or_create_project_json(
        cf_dir=str(cf_dir),
        gds_path=collected["gds/user_project_wrapper.gds"],
        cli_overrides=cli_overrides,
        existing_json_path=collected.get(".cf/project.json")
    )
    console.print(f"[OK] project.json ready: {project_json_path}", style="green")

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
        console.print("[DRY-RUN] The following files would be uploaded:", style="bold magenta")
        for rel_path, local_path in upload_map.items():
            if local_path:
                remote_path = os.path.join(sftp_base, rel_path)
                console.print(f"  {local_path} -> {remote_path}", style="magenta")
        console.print("[DRY-RUN] No files were uploaded.", style="bold magenta")
        return

    console.print(f"[INFO] Connecting to SFTP: {sftp_host} as {sftp_username}", style="bold cyan")
    transport = None
    try:
        sftp, transport = sftp_connect(
            host=sftp_host,
            username=sftp_username,
            password=password,
            key_path=key_path
        )
        # Ensure the project directory exists before uploading
        sftp_project_dir = f"incoming/projects/{final_project_name}"
        sftp_ensure_dirs(sftp, sftp_project_dir)
    except Exception as e:
        console.print(f"[ERROR] Failed to connect to SFTP: {e}", style="bold red")
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
        console.print(f"[SUCCESS] All files uploaded to {sftp_base}", style="bold green")
    except Exception as e:
        console.print(f"[ERROR] SFTP upload failed: {e}", style="bold red")
        raise click.Abort()
    finally:
        if transport:
            sftp.close()
            transport.close()

if __name__ == "__main__":
    main() 