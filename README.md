# ChipFoundry CLI (`cf-cli`)

A command-line tool to automate the submission of ChipFoundry projects to the SFTP server.

---

## Overview

`cf-cli` is a user-friendly command-line tool for securely submitting your ChipFoundry project files to the official SFTP server. It automatically collects the required files, generates or updates your project configuration, and uploads everything to the correct location on the server.

---

## Installation

Install from PyPI:

```bash
pip install cf-cli
cf --help
```

---

## Project Structure Requirements

Your project directory **must** contain:

- `gds/` directory with **one** of the following:
  - `user_project_wrapper.gds` (for digital projects)
  - `user_analog_project_wrapper.gds` (for analog projects)
  - `openframe_project_wrapper.gds` (for openframe projects)
- `verilog/rtl/user_defines.v` (required for digital/analog)
- `.cf/project.json` (optional; will be created/updated automatically)

**Example:**
```
my_project/
├── gds/
│   └── user_project_wrapper.gds
├── verilog/
│   └── rtl/
│       └── user_defines.v
└── .cf/
    └── project.json
```

---

## Authentication

- By default, the tool will look for an SSH key at `~/.ssh/id_rsa`.
- You can specify a different key with `--sftp-key`.
- If no key is found, you will be prompted to enter a key path or your SFTP password.
- Your SFTP username is required (provided by ChipFoundry).

---

## SFTP Server

- The default SFTP server is `sftp.chipfoundry.io` (no need to specify unless you want to override).

---

## Usage

### Basic Submission (Digital Project)

```bash
cf submit --project-root /path/to/my_project --sftp-username <your_chipfoundry_username>
```

### With a Custom SSH Key

```bash
cf submit --project-root /path/to/my_project --sftp-username <your_chipfoundry_username> --sftp-key /path/to/id_rsa
```

### With Password Authentication

```bash
cf submit --project-root /path/to/my_project --sftp-username <your_chipfoundry_username> --sftp-password <your_password>
```

### Dry Run (Preview what will be uploaded)

```bash
cf submit --project-root /path/to/my_project --sftp-username <your_chipfoundry_username> --dry-run
```

### Override Project Name or ID

```bash
cf submit --project-root /path/to/my_project --sftp-username <your_chipfoundry_username> --project-name my_custom_name --project-id my_custom_id
```

---

## What Happens When You Run `cf submit`?

1. **File Collection:**
   - The tool checks for the required GDS and Verilog files.
   - It auto-detects your project type (digital, analog, openframe) based on the GDS file name.
2. **Configuration:**
   - If `.cf/project.json` does not exist, it is created.
   - The tool updates the GDS hash and any fields you override via CLI.
3. **SFTP Upload:**
   - Connects to the SFTP server as your user.
   - Ensures the directory `incoming/projects/<project_name>` exists.
   - Uploads `.cf/project.json`, the GDS file, and `verilog/rtl/user_defines.v` (if present).
   - Shows a progress bar for each file upload.
4. **Success:**
   - You’ll see a green success message when all files are uploaded.

---

## Troubleshooting

- **Missing files:**
  - The tool will error out if required files are missing or if more than one GDS type is present.
- **Authentication errors:**
  - Make sure your SSH key is valid and registered with ChipFoundry, or use your password.
- **SFTP errors:**
  - Check your network connection and credentials.
- **Project type detection:**
  - Only one of the recognized GDS files should be present in your `gds/` directory.

---

## Support

- For help, contact info@chipfoundry.io or visit [chipfoundry.io](https://chipfoundry.io)
- For bug reports or feature requests, open an issue on [GitHub](https://github.com/chipfoundry/cf-cli)
