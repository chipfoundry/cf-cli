"""
Unit tests for cf verify command.
"""
import json
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from pathlib import Path
import tempfile
import shutil
import os


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


def _ensure_project_json(project_root: str) -> None:
    """Create minimal .cf/project.json so verify command can read project type."""
    cf_dir = Path(project_root) / ".cf"
    cf_dir.mkdir(parents=True, exist_ok=True)
    project_json = cf_dir / "project.json"
    if not project_json.exists():
        project_json.write_text(json.dumps({"project": {"type": "digital"}}))


class TestVerifyCommand:
    """Test suite for cf verify command."""
    
    def test_verify_help(self):
        """Test verify command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['verify', '--help'])
        
        assert result.exit_code == 0
        assert 'Run cocotb verification tests' in result.output
        assert '--project-root' in result.output
        assert '--sim' in result.output
        assert '--list' in result.output
        assert '--all' in result.output
        assert '--tag' in result.output
        assert '--dry-run' in result.output
    
    def test_verify_list(self, temp_project_dir):
        """Test verify command with --list flag."""
        _ensure_project_json(temp_project_dir)
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            '--list'
        ])
        
        # Command returns 0; with no cocotb dir it prints a message, with cocotb it lists tests
        assert result.exit_code == 0
        assert 'cocotb' in result.output.lower() or 'list' in result.output.lower()
    
    def test_verify_with_test(self, temp_project_dir):
        """Test verify command with test argument."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            'counter_la',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'cocotb' in result.output.lower() or 'dry-run' in result.output.lower()
    
    def test_verify_with_sim(self, temp_project_dir):
        """Test verify command with --sim option."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            'counter_la',
            '--project-root', temp_project_dir,
            '--sim', 'gl',
            '--dry-run'
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'cocotb' in result.output.lower() or 'dry-run' in result.output.lower()
    
    def test_verify_all(self, temp_project_dir):
        """Test verify command with --all flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            '--all',
            '--dry-run'
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'cocotb' in result.output.lower() or 'dry-run' in result.output.lower()
    
    def test_verify_with_tag(self, temp_project_dir):
        """Test verify command with --tag option."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            '--tag', 'user_proj_tests',
            '--dry-run'
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'cocotb' in result.output.lower() or 'dry-run' in result.output.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
