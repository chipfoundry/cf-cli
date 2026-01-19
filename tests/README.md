# Test Suite Documentation

## What Makes Tests Meaningful?

### 1. **Functional Tests** (`test_functional.py`)
These tests verify actual command behavior:
- **Argument Parsing**: Tests that arguments are correctly parsed and used (e.g., `--pdk sky130B` is actually recognized)
- **Validation**: Tests that invalid inputs are rejected (e.g., invalid `--sim` choices)
- **Error Handling**: Tests that commands fail gracefully with meaningful errors
- **Logic Flow**: Tests that command logic works correctly (e.g., `--only-caravel` limits scope)

### 2. **Utility Function Tests** (`test_utils.py`)
These tests verify core utility functions that can be tested in isolation:
- **File Collection**: Tests that `collect_project_files()` correctly identifies and validates project files
- **GDS Type Detection**: Tests that different GDS types (digital, analog, openframe) are correctly identified
- **Error Cases**: Tests that invalid configurations (multiple GDS types, compressed+uncompressed) are rejected
- **Hash Calculation**: Tests that SHA256 hashing works correctly
- **JSON Operations**: Tests that project.json loading/saving works correctly

### 3. **Command Interface Tests** (existing files)
These tests verify the CLI interface:
- **Help Text**: Ensures all commands have help text
- **Command Discovery**: Ensures all commands are accessible
- **Argument Recognition**: Ensures all arguments are recognized

## Test Coverage

### Current Coverage
- **Utils Functions**: ~34% coverage (testable functions)
- **Main Commands**: ~17% coverage (limited by external dependencies)
- **Total**: ~17% overall coverage

### Why Coverage is Lower for Commands
Many commands require:
- SFTP connections (push, pull, status, confirm)
- External tools (Docker, Nix, Git)
- Network access (version fetching)
- Large dependencies (PDK, Caravel, OpenLane)

These are difficult to test in CI without mocking or integration test infrastructure.

## What We Test

### ✅ What's Tested
1. **All 14 commands** are accessible and have help text
2. **All command arguments** are recognized and parsed
3. **Utility functions** work correctly (file collection, validation, hashing)
4. **Error handling** works for missing files/dependencies
5. **Argument validation** rejects invalid inputs
6. **Command logic** (e.g., --only-* flags limit scope)

### ❌ What's Not Tested (and why)
1. **SFTP operations** - Requires real SFTP server or complex mocking
2. **External tool execution** - Requires Docker/Nix/Git to be installed
3. **Network operations** - Version fetching requires GitHub API access
4. **Full command execution** - Many commands require large dependencies

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_utils.py -v

# Run with coverage
pytest tests/ --cov=chipfoundry_cli --cov-report=html

# View coverage report
open htmlcov/index.html
```

## Coverage Reports

Coverage reports are:
1. **Generated** as HTML in `htmlcov/` directory
2. **Uploaded** as GitHub Actions artifacts (downloadable from workflow runs)
3. **Summarized** in PR comments via GitHub Step Summary

No external service (like Codecov) is required - everything is handled by GitHub Actions.
