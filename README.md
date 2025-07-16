# ChipFoundry CLI Tool

A Python CLI tool to automate the submission of ChipFoundry projects to an SFTP server. Collects project files, generates/updates project.json, and uploads to the correct SFTP directory.

## Installation

```bash
pip install .
```

## Usage

```bash
chipfoundry-cli --project-root <path> --sftp-host <hostname> --sftp-username <username> [options]
```

### Required Project Structure

Your project directory should contain:

- `.cf/project.json` (optional, will be created/updated if missing)
- `gds/user_project_wrapper.gds` (required)
- `verilog/rtl/user_defines.v` (required)

### Example (Dry Run)

```bash
chipfoundry-cli --project-root my_project --sftp-host sftp.example.com --sftp-username user123 --project-name my_proj --dry-run
```

### Options

- `--sftp-password <password>`: SFTP password (prompted if not provided)
- `--sftp-key <path>`: Path to SFTP private key (alternative to password)
- `--project-id <id>`: Override project ID
- `--project-name <name>`: Override project name
- `--project-type <type>`: Override project type
- `--project-slot <slot>`: Override project slot
- `--force-overwrite`: Overwrite existing files on SFTP
- `--dry-run`: Preview actions without uploading files

### Features

- **Progress Bar**: Shows upload progress (requires `tqdm`)
- **Dry Run**: Preview what would be uploaded
- **Robust Error Handling**: Clear messages for missing files, SFTP errors, and more
