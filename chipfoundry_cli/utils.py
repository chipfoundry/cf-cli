import os
import shutil
from pathlib import Path
from typing import Dict, Optional, Any
import json
import hashlib
import paramiko
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TaskProgressColumn
import toml
import httpx
import re

REQUIRED_FILES = {
    ".cf/project.json": False,  # Optional, may not exist
    "verilog/rtl/user_defines.v": True,
}

# GDS files for different project types (both compressed and uncompressed)
GDS_TYPE_MAP = {
    'user_project_wrapper.gds': 'digital',
    'user_project_wrapper.gds.gz': 'digital',
    'user_analog_project_wrapper.gds': 'analog',
    'user_analog_project_wrapper.gds.gz': 'analog',
    'openframe_project_wrapper.gds': 'openframe',
    'openframe_project_wrapper.gds.gz': 'openframe',
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
        
        # Group by project type
        project_type_files = {}
        for gds_name, gds_path in found_gds_files:
            project_type = GDS_TYPE_MAP[gds_name]
            if project_type not in project_type_files:
                project_type_files[project_type] = []
            project_type_files[project_type].append((gds_name, gds_path))
        
        if len(project_type_files) > 1:
            found_types = list(project_type_files.keys())
            raise FileNotFoundError(f"Multiple project types found: {found_types}. Only one project type is allowed per project.")
        
        # For the single project type, check if both compressed and uncompressed versions exist
        project_type = list(project_type_files.keys())[0]
        type_files = project_type_files[project_type]
        
        # Check for both compressed and uncompressed versions of the same file
        compressed_files = [f for f in type_files if f[0].endswith('.gz')]
        uncompressed_files = [f for f in type_files if not f[0].endswith('.gz')]
        
        if len(compressed_files) > 0 and len(uncompressed_files) > 0:
            # Find the base name without extension to show which file has both versions
            base_names = set()
            for gds_name, _ in type_files:
                base_name = gds_name.replace('.gz', '')
                base_names.add(base_name)
            
            if len(base_names) == 1:
                # Same base file has both compressed and uncompressed versions
                base_name = list(base_names)[0]
                compressed_name = f"{base_name}.gz"
                uncompressed_name = base_name
                raise FileNotFoundError(
                    f"Both compressed and uncompressed versions of the same GDS file found: "
                    f"'{compressed_name}' and '{uncompressed_name}'. "
                    f"Please remove one of them and keep only one version."
                )
        
        # Find uncompressed file first, then fall back to compressed
        gds_file_to_use = None
        for gds_name, gds_path in type_files:
            if not gds_name.endswith('.gz'):
                gds_file_to_use = (gds_name, gds_path)
                break
        
        # If no uncompressed file found, use the first available (compressed)
        if not gds_file_to_use:
            gds_file_to_use = type_files[0]
        
        gds_name, gds_path = gds_file_to_use
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
    Otherwise, create a new one. Always update the user_project_wrapper_hash and auto-increment version.
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
    
    # Handle version auto-increment only if GDS hash changed
    current_version = data["project"].get("version")
    current_hash = data["project"].get("user_project_wrapper_hash", "")
    
    if current_version is None:
        # No existing version, start with 1
        new_version = "1"
    elif current_hash != hash_val:
        # GDS hash changed, increment version
        try:
            # Convert to int, increment, and convert back to string
            version_num = int(current_version)
            new_version = str(version_num + 1)
        except (ValueError, TypeError):
            # If version is not a valid integer, start from 1
            new_version = "1"
    else:
        # GDS hash unchanged, keep same version
        new_version = current_version
    
    # Required fields with defaults
    data["project"]["version"] = new_version
    data["project"]["user_project_wrapper_hash"] = hash_val
    
    # Apply CLI overrides (but don't override auto-incremented version)
    for key in ["id", "name", "type", "user"]:  # Removed "version" from CLI overrides
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



def download_with_progress(sftp, remote_path, local_path, console=None):
    """
    Download a file with a rich progress bar.
    """
    try:
        remote_stat = sftp.stat(remote_path)
        file_size = remote_stat.st_size
        
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total} bytes"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task(f"Downloading {os.path.basename(remote_path)}", total=file_size)
            
            def progress_cb(bytes_transferred, total):
                progress.update(task, completed=bytes_transferred)
            
            # Ensure local directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Download file with progress
            with open(local_path, "wb") as f:
                def callback(bytes_transferred, total=file_size):
                    progress_cb(bytes_transferred, total)
                sftp.getfo(remote_path, f, callback=callback)
            
            progress.update(task, completed=file_size)
            return True
            
    except Exception as e:
        if console:
            console.print(f"[red]Failed to download {os.path.basename(remote_path)}: {e}[/red]")
        raise

def sftp_download_recursive(sftp, remote_path: str, local_path: str, progress_cb=None, console=None):
    """
    Recursively download files and directories from SFTP server.
    
    Args:
        sftp: SFTP client
        remote_path: Remote path on SFTP server
        local_path: Local path to save to
        progress_cb: Optional progress callback function(bytes_transferred, total_bytes)
        console: Optional rich console for logging
    """
    try:
        # Get remote file/directory stats
        remote_stat = sftp.stat(remote_path)
        
        if remote_stat.st_mode & 0o40000:  # Directory
            # Create local directory
            os.makedirs(local_path, exist_ok=True)
            if console:
                console.print(f"[dim]Creating directory: {os.path.basename(local_path)}[/dim]")
            
            # List contents and download recursively
            try:
                remote_contents = sftp.listdir(remote_path)
                if console:
                    console.print(f"[dim]Found {len(remote_contents)} items in {os.path.basename(remote_path)}[/dim]")
                for item in remote_contents:
                    remote_item_path = f"{remote_path}/{item}"
                    local_item_path = os.path.join(local_path, item)
                    sftp_download_recursive(sftp, remote_item_path, local_item_path, progress_cb, console)
            except Exception as e:
                if console:
                    console.print(f"[yellow]Warning: Could not list directory {os.path.basename(remote_path)}: {e}[/yellow]")
                
        else:  # File
            # Ensure local directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            if console:
                console.print(f"[dim]Downloading: {os.path.basename(remote_path)} ({remote_stat.st_size:,} bytes)[/dim]")
            
            # Download file with progress if callback provided
            if progress_cb:
                file_size = remote_stat.st_size
                with open(local_path, "wb") as f:
                    def callback(bytes_transferred, total=file_size):
                        progress_cb(bytes_transferred, total)
                    sftp.getfo(remote_path, f, callback=callback)
            else:
                sftp.get(remote_path, local_path)
                
    except Exception as e:
        if console:
            console.print(f"[red]Error downloading {os.path.basename(remote_path)}: {e}[/red]")
        raise

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

def open_html_in_browser(html_path: str):
    """
    Open an HTML file in the default browser.
    
    Args:
        html_path: Path to the HTML file to open
    """
    import webbrowser
    import urllib.parse
    
    # Convert to absolute path and file URL
    abs_path = os.path.abspath(html_path)
    file_url = f"file://{urllib.parse.quote(abs_path)}"
    
    # Open in default browser
    webbrowser.open(file_url)

def fetch_github_file(repo_owner: str, repo_name: str, file_path: str, branch: str = "main") -> str:
    """
    Fetch a file from a GitHub repository using the GitHub API.
    
    Args:
        repo_owner: GitHub repository owner (e.g., "chipfoundry")
        repo_name: GitHub repository name (e.g., "caravel_user_project")
        file_path: Path to the file in the repository (e.g., ".cf/repo.json")
        branch: Branch name (default: "main")
    
    Returns:
        File content as string
    
    Raises:
        httpx.HTTPError: If the request fails
    """
    url = f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{branch}/{file_path}"
    
    with httpx.Client() as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text

def fetch_versions_from_upstream(repo_owner: str = "chipfoundry", repo_name: str = "cf-cli", branch: str = "main") -> Dict:
    """
    Fetch version information from the cf-cli repository.
    
    Args:
        repo_owner: GitHub repository owner (default: "chipfoundry")
        repo_name: GitHub repository name (default: "cf-cli")
        branch: Branch name (default: "main")
    
    Returns:
        Dictionary with version information
    
    Raises:
        httpx.HTTPError: If the request fails
        json.JSONDecodeError: If the file is not valid JSON
        KeyError: If required version fields are missing
    """
    versions_content = fetch_github_file(repo_owner, repo_name, "versions.json", branch)
    versions = json.loads(versions_content)
    
    # Validate required fields
    required_fields = ['mpw_tags', 'openlane_version', 'open_pdks_commits']
    missing_fields = [field for field in required_fields if field not in versions]
    if missing_fields:
        raise KeyError(f"Missing required version fields: {', '.join(missing_fields)}")
    
    return versions

def download_github_file(repo_owner: str, repo_name: str, file_path: str, local_path: str, branch: str = "main") -> bool:
    """
    Download a file from a GitHub repository and save it locally.
    
    Args:
        repo_owner: GitHub repository owner
        repo_name: GitHub repository name
        file_path: Path to the file in the repository
        local_path: Local path to save the file
        branch: Branch name (default: "main")
    
    Returns:
        True if successful, False otherwise
    """
    try:
        content = fetch_github_file(repo_owner, repo_name, file_path, branch)
        
        # Ensure the local directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        # Write the content to the local file
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return True
    except Exception:
        return False

def update_repo_files(project_root: str, repo_owner: str = "chipfoundry", repo_name: str = "caravel_user_project", branch: str = "main") -> Dict[str, bool]:
    """
    Update local repository files based on the repo.json changes list.
    
    Args:
        project_root: Local project root directory
        repo_owner: GitHub repository owner
        repo_name: GitHub repository name
        branch: Branch name containing the repo.json file
    
    Returns:
        Dictionary mapping file paths to success status
    """
    results = {}
    
    try:
        # Fetch the repo.json file
        repo_json_content = fetch_github_file(repo_owner, repo_name, ".cf/repo.json", branch)
        repo_data = json.loads(repo_json_content)
        
        # Save the repo.json file to local .cf directory
        cf_dir = os.path.join(project_root, ".cf")
        os.makedirs(cf_dir, exist_ok=True)
        local_repo_json_path = os.path.join(cf_dir, "repo.json")
        
        with open(local_repo_json_path, 'w', encoding='utf-8') as f:
            f.write(repo_json_content)
        results[".cf/repo.json"] = True
        
        changes = repo_data.get("changes", [])
        
        for file_path in changes:
            local_file_path = os.path.join(project_root, file_path)
            success = download_github_file(repo_owner, repo_name, file_path, local_file_path, branch)
            results[file_path] = success
            
    except Exception as e:
        # If we can't fetch the repo.json, return empty results
        results["error"] = str(e)
    
    return results

# GPIO Configuration Helpers

GPIO_MODES = {
    "invalid": "GPIO_MODE_INVALID",
    "mgmt_input_nopull": "GPIO_MODE_MGMT_STD_INPUT_NOPULL",
    "mgmt_input_pulldown": "GPIO_MODE_MGMT_STD_INPUT_PULLDOWN",
    "mgmt_input_pullup": "GPIO_MODE_MGMT_STD_INPUT_PULLUP",
    "mgmt_output": "GPIO_MODE_MGMT_STD_OUTPUT",
    "mgmt_bidirectional": "GPIO_MODE_MGMT_STD_BIDIRECTIONAL",
    "mgmt_analog": "GPIO_MODE_MGMT_STD_ANALOG",
    "user_input_nopull": "GPIO_MODE_USER_STD_INPUT_NOPULL",
    "user_input_pulldown": "GPIO_MODE_USER_STD_INPUT_PULLDOWN",
    "user_input_pullup": "GPIO_MODE_USER_STD_INPUT_PULLUP",
    "user_output": "GPIO_MODE_USER_STD_OUTPUT",
    "user_bidirectional": "GPIO_MODE_USER_STD_BIDIRECTIONAL",
    "user_output_monitored": "GPIO_MODE_USER_STD_OUT_MONITORED",
    "user_analog": "GPIO_MODE_USER_STD_ANALOG",
}

# Mapping from mode names to hex values
GPIO_MODE_TO_HEX = {
    "GPIO_MODE_INVALID": "13'hXXXX",
    "GPIO_MODE_MGMT_STD_INPUT_NOPULL": "13'h0403",
    "GPIO_MODE_MGMT_STD_INPUT_PULLDOWN": "13'h0c01",
    "GPIO_MODE_MGMT_STD_INPUT_PULLUP": "13'h0801",
    "GPIO_MODE_MGMT_STD_OUTPUT": "13'h1809",
    "GPIO_MODE_MGMT_STD_BIDIRECTIONAL": "13'h1801",
    "GPIO_MODE_MGMT_STD_ANALOG": "13'h000b",
    "GPIO_MODE_USER_STD_INPUT_NOPULL": "13'h0402",
    "GPIO_MODE_USER_STD_INPUT_PULLDOWN": "13'h0c00",
    "GPIO_MODE_USER_STD_INPUT_PULLUP": "13'h0800",
    "GPIO_MODE_USER_STD_OUTPUT": "13'h1808",
    "GPIO_MODE_USER_STD_BIDIRECTIONAL": "13'h1800",
    "GPIO_MODE_USER_STD_OUT_MONITORED": "13'h1802",
    "GPIO_MODE_USER_STD_ANALOG": "13'h000a",
}

# Reverse mapping from hex values to mode names
GPIO_HEX_TO_MODE = {hex_val: mode_name for mode_name, hex_val in GPIO_MODE_TO_HEX.items()}

GPIO_MODE_DESCRIPTIONS = {
    "invalid": "Invalid (13'hXXXX) - Placeholder, must be changed",
    "mgmt_input_nopull": "Management Input No Pull (13'h0403)",
    "mgmt_input_pulldown": "Management Input Pull Down (13'h0c01)",
    "mgmt_input_pullup": "Management Input Pull Up (13'h0801)",
    "mgmt_output": "Management Output (13'h1809)",
    "mgmt_bidirectional": "Management Bidirectional (13'h1801)",
    "mgmt_analog": "Management Analog (13'h000b)",
    "user_input_nopull": "User Input No Pull (13'h0402)",
    "user_input_pulldown": "User Input Pull Down (13'h0c00)",
    "user_input_pullup": "User Input Pull Up (13'h0800)",
    "user_output": "User Output (13'h1808)",
    "user_bidirectional": "User Bidirectional (13'h1800)",
    "user_output_monitored": "User Output Monitored (13'h1802)",
    "user_analog": "User Analog (13'h000a)",
}

def parse_user_defines_v(file_path: str) -> Dict[int, str]:
    """
    Parse user_defines.v file to extract GPIO configurations.
    Returns a dict mapping GPIO number (5-37) to mode define name (e.g., "GPIO_MODE_USER_STD_OUTPUT").
    """
    gpio_configs = {}
    if not Path(file_path).exists():
        return gpio_configs
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Extract GPIO configurations using regex
    # Pattern to match: `define USER_CONFIG_GPIO_<num>_INIT  `GPIO_MODE_<MODE> or hex value
    pattern = r'`define\s+USER_CONFIG_GPIO_(\d+)_INIT\s+`?([A-Z0-9_]+|0x[0-9a-fA-F]+|13\'h[0-9a-fA-FX]+)'
    
    for match in re.finditer(pattern, content):
        gpio_num = int(match.group(1))
        mode_value = match.group(2)
        # If it's a hex value or invalid, keep it as is
        # Otherwise, it should be a mode name like GPIO_MODE_USER_STD_OUTPUT
        if mode_value.startswith('0x') or mode_value.startswith("13'h") or mode_value == 'GPIO_MODE_INVALID':
            # Keep hex values and invalid as is - they'll be handled in the UI
            gpio_configs[gpio_num] = mode_value
        else:
            # It's a mode name, use it directly
            gpio_configs[gpio_num] = mode_value
    
    return gpio_configs

def update_user_defines_v(file_path: str, gpio_configs: Dict[int, str]):
    """
    Update user_defines.v file with new GPIO configurations.
    gpio_configs maps GPIO number (5-37) to mode define name (e.g., "GPIO_MODE_USER_STD_OUTPUT").
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"user_defines.v not found at {file_path}")
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Update each GPIO configuration line
    # Pattern matches: `define USER_CONFIG_GPIO_<num>_INIT  `<mode>
    pattern = r'(`define\s+USER_CONFIG_GPIO_)(\d+)(_INIT\s+)(.+)'
    
    new_lines = []
    for line in lines:
        match = re.match(pattern, line)
        if match:
            gpio_num = int(match.group(2))
            if gpio_num in gpio_configs:
                # Replace the mode value - preserve the backtick before the mode name
                mode = gpio_configs[gpio_num]
                # Preserve original spacing/formatting
                indent = match.group(3)  # This includes the spacing after _INIT
                new_line = f"{match.group(1)}{gpio_num}{indent}`{mode}\n"
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    with open(file_path, 'w') as f:
        f.writelines(new_lines)

def get_gpio_config_from_project_json(project_json_path: str) -> Optional[Dict[int, str]]:
    """
    Load GPIO configuration from project.json.
    Returns a dict mapping GPIO number to mode name (converts hex to mode name if needed).
    """
    if not Path(project_json_path).exists():
        return None
    
    data = load_project_json(project_json_path)
    gpio_config = data.get("project", {}).get("gpio_config")
    if not gpio_config:
        return None
    
    # Convert hex values to mode names if needed
    converted_config = {}
    for gpio_num_str, value in gpio_config.items():
        gpio_num = int(gpio_num_str)
        # If it's already a hex value, convert to mode name
        if value in GPIO_HEX_TO_MODE:
            converted_config[gpio_num] = GPIO_HEX_TO_MODE[value]
        # If it's a mode name, keep it
        elif value in GPIO_MODE_TO_HEX:
            converted_config[gpio_num] = value
        # If it's a hex value in different format, try to match
        elif value.startswith("13'h") or value.startswith("0x"):
            # Try to find matching hex value
            for hex_val, mode_name in GPIO_HEX_TO_MODE.items():
                if hex_val.replace("13'h", "").upper() == value.replace("13'h", "").replace("0x", "").upper():
                    converted_config[gpio_num] = mode_name
                    break
            else:
                # Keep as is if we can't convert
                converted_config[gpio_num] = value
        else:
            # Keep as is
            converted_config[gpio_num] = value
    
    return converted_config

def save_gpio_config_to_project_json(project_json_path: str, gpio_configs: Dict[int, str]):
    """
    Save GPIO configuration to project.json.
    Converts mode names to hex values before saving.
    """
    if Path(project_json_path).exists():
        data = load_project_json(project_json_path)
    else:
        data = {"project": {}}
    
    if "project" not in data:
        data["project"] = {}
    
    # Convert mode names to hex values
    hex_config = {}
    for gpio_num, mode_name in gpio_configs.items():
        # Convert mode name to hex if we have a mapping
        if mode_name in GPIO_MODE_TO_HEX:
            hex_config[str(gpio_num)] = GPIO_MODE_TO_HEX[mode_name]
        else:
            # Keep as is if it's already a hex value or unknown
            hex_config[str(gpio_num)] = mode_name
    
    data["project"]["gpio_config"] = hex_config
    save_project_json(project_json_path, data)