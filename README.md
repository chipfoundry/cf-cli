# ChipFoundry CLI (`cf-cli`)

A command-line tool to automate the submission of ChipFoundry projects to the SFTP server.

---

## Overview

`cf-cli` is a user-friendly command-line tool for securely submitting your ChipFoundry project files to the official SFTP server. It automatically collects the required files, generates or updates your project configuration, and uploads everything to the correct location on the server.

---

## Installation

Install from PyPI:

```bash
pip install chipfoundry-cli
chipfoundry --help
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

### Configure User Credentials

```bash
chipfoundry config
```
- Prompts for your SFTP username and key path. Only needs to be run once per user/machine.

### Initialize a New Project

```bash
chipfoundry init
```
- Prompts for project name, type (auto-detected from GDS file if present), and version.
- Creates `.cf/project.json` in the current directory.
- **Note:** The GDS hash is NOT generated at this step (see below).

### Push a Project (Upload)

```bash
chipfoundry push
```
- Run from your project directory (with `.cf/project.json`).
- Collects files, updates the GDS hash, and uploads to SFTP.

### Pull Results

```bash
chipfoundry pull
```
- Downloads results for the current project to a local directory.

### Check Status

```bash
chipfoundry status
```
- Shows all your projects and their input/output status on the SFTP server.

---

## How the GDS Hash Works

- The `user_project_wrapper_hash` in `.cf/project.json` is **automatically generated and updated during `push`**.
- The hash is calculated from the actual GDS file being uploaded.
- This ensures the hash always matches the file you are submitting.
- **You do not need to manage or update the hash manually.**
- The hash is NOT generated during `init` because the GDS file may not exist or may change before submission.

---

## What Happens When You Run `chipfoundry push`?

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
- **ModuleNotFoundError: No module named 'toml':**
  - This means your environment is missing the `toml` dependency. Upgrade `chipfoundry-cli` with `pip install --upgrade chipfoundry-cli`, or install `toml` manually with `pip install 'toml>=0.10,<1.0'`.

---

## Support

- For help, contact info@chipfoundry.io or visit [chipfoundry.io](https://chipfoundry.io)
- For bug reports or feature requests, open an issue on [GitHub](https://github.com/chipfoundry/cf-cli)
