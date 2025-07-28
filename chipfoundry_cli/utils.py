import os
import shutil
from pathlib import Path
from typing import Dict, Optional
import json
import hashlib
import paramiko
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TaskProgressColumn
import toml

REQUIRED_FILES = {
    ".cf/project.json": False,  # Optional, may not exist
    "verilog/rtl/user_defines.v": True,
}

# GDS files for different project types
GDS_TYPE_MAP = {
    'user_project_wrapper.gds': 'digital',
    'user_analog_project_wrapper.gds': 'analog',
    'openframe_project_wrapper.gds': 'openframe',
}

def collect_project_files(project_root: str) -> Dict[str, Optional[str]]:
    """
    Collect required project files from the given project_root.
    Returns a dict mapping logical names to absolute file paths (or None if not found and optional).
    Raises FileNotFoundError if any required file is missing.
    """
    project_root = Path(project_root)
    collected = {}
    
    # Collect standard required files
    for rel_path, required in REQUIRED_FILES.items():
        abs_path = project_root / rel_path
        if abs_path.exists():
            collected[rel_path] = str(abs_path)
        elif required:
            raise FileNotFoundError(f"Required file not found: {abs_path}")
        else:
            collected[rel_path] = None
    
    # Collect GDS file based on what exists
    gds_dir = project_root / 'gds'
    if gds_dir.exists():
        found_gds_files = []
        for gds_name in GDS_TYPE_MAP.keys():
            gds_path = gds_dir / gds_name
            if gds_path.exists():
                found_gds_files.append((gds_name, str(gds_path)))
        
        if len(found_gds_files) == 0:
            raise FileNotFoundError(f"No GDS file found in {gds_dir}. Expected one of: {list(GDS_TYPE_MAP.keys())}")
        elif len(found_gds_files) > 1:
            found_names = [name for name, _ in found_gds_files]
            raise FileNotFoundError(f"Multiple GDS files found: {found_names}. Only one project type is allowed per project.")
        else:
            gds_name, gds_path = found_gds_files[0]
            collected[f"gds/{gds_name}"] = gds_path
    
    return collected

def ensure_cf_directory(target_dir: str):
    """
    Ensure the .cf directory exists in the target directory.
    """
    cf_dir = Path(target_dir) / ".cf"
    cf_dir.mkdir(parents=True, exist_ok=True)
    return cf_dir

def copy_files_to_temp(collected: Dict[str, Optional[str]], temp_dir: str):
    """
    Copy collected files to a temporary directory, preserving structure.
    """
    for rel_path, abs_path in collected.items():
        if abs_path:
            dest_path = Path(temp_dir) / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest_path)

def calculate_sha256(file_path: str) -> str:
    """
    Calculate SHA256 hash of the given file.
    """
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()

def load_project_json(json_path: str) -> dict:
    """
    Load project.json from the given path.
    """
    with open(json_path, 'r') as f:
        return json.load(f)

def save_project_json(json_path: str, data: dict):
    """
    Save the project.json to the given path (pretty-printed).
    """
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=2)

def update_or_create_project_json(
    cf_dir: str,
    gds_path: str,
    cli_overrides: dict,
    existing_json_path: Optional[str] = None
) -> str:
    """
    Update or create project.json in cf_dir. If existing_json_path is given, load and update it.
    Otherwise, create a new one. Always update the user_project_wrapper_hash.
    Returns the path to the updated/created project.json.
    """
    project_json_path = str(Path(cf_dir) / "project.json")
    hash_val = calculate_sha256(gds_path)
    if existing_json_path and Path(existing_json_path).exists():
        data = load_project_json(existing_json_path)
        if "project" not in data:
            data["project"] = {}
    else:
        data = {"project": {}}
    # Required fields with defaults
    data["project"].setdefault("version", "1.0.0")
    data["project"]["user_project_wrapper_hash"] = hash_val
    # Apply CLI overrides
    for key in ["id", "name", "type", "user", "version"]:
        cli_key = f"project_{key}" if key != "user" else "sftp_username"
        if cli_key in cli_overrides and cli_overrides[cli_key] is not None:
            data["project"][key] = cli_overrides[cli_key]
    save_project_json(project_json_path, data)
    return project_json_path 

def load_private_key(key_path, password=None):
    key_loaders = [
        paramiko.Ed25519Key.from_private_key_file,
        paramiko.RSAKey.from_private_key_file,
        paramiko.ECDSAKey.from_private_key_file,
        paramiko.DSSKey.from_private_key_file,
    ]
    last_exception = None
    for loader in key_loaders:
        try:
            return loader(key_path, password=password)
        except paramiko.ssh_exception.PasswordRequiredException:
            raise  # Key is encrypted, need password
        except Exception as e:
            last_exception = e
    raise RuntimeError(f"Could not load private key: {last_exception}")

def sftp_connect(host: str, username: str, key_path: str):
    """
    Establish an SFTP connection using paramiko. Returns an SFTP client.
    """
    transport = paramiko.Transport((host, 22))
    private_key = load_private_key(key_path)
    transport.connect(username=username, pkey=private_key)
    sftp = paramiko.SFTPClient.from_transport(transport)
    return sftp, transport

def sftp_ensure_dirs(sftp, remote_path: str):
    """
    Recursively create directories on the SFTP server if they do not exist.
    """
    dirs = []
    path = remote_path
    while len(path) > 1:
        dirs.append(path)
        path, _ = os.path.split(path)
    dirs = dirs[::-1]
    for d in dirs:
        try:
            sftp.stat(d)
        except FileNotFoundError:
            try:
                sftp.mkdir(d)
            except Exception:
                pass

def sftp_upload_file(sftp, local_path: str, remote_path: str, force_overwrite: bool = False, progress_cb=None):
    """
    Upload a file to the SFTP server, optionally overwriting. Optionally report progress via progress_cb(bytes_transferred, total_bytes).
    """
    try:
        if not force_overwrite:
            sftp.stat(remote_path)
            print(f"File exists on SFTP: {remote_path}. Skipping (use --force-overwrite to overwrite).")
            return False
    except FileNotFoundError:
        pass  # File does not exist, proceed
    sftp_ensure_dirs(sftp, os.path.dirname(remote_path))
    if progress_cb:
        file_size = os.path.getsize(local_path)
        with open(local_path, 'rb') as f:
            def callback(bytes_transferred, total=file_size):
                progress_cb(bytes_transferred, total)
            sftp.putfo(f, remote_path, callback=callback)
    else:
        sftp.put(local_path, remote_path)
    return True

def upload_with_progress(sftp, local_path, remote_path, force_overwrite=False):
    """
    Upload a file with a rich progress bar.
    """
    file_size = os.path.getsize(local_path)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("{task.completed}/{task.total} bytes"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task(f"Uploading {os.path.basename(local_path)}", total=file_size)
        def progress_cb(bytes_transferred, total):
            progress.update(task, completed=bytes_transferred)
        result = sftp_upload_file(sftp, local_path, remote_path, force_overwrite, progress_cb=progress_cb)
        progress.update(task, completed=file_size)
        return result 

def get_config_path() -> Path:
    return Path.home() / ".chipfoundry-cli" / "config.toml"

def load_user_config() -> dict:
    config_path = get_config_path()
    if config_path.exists():
        return toml.load(config_path)
    return {}

def save_user_config(config: dict):
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        toml.dump(config, f) 