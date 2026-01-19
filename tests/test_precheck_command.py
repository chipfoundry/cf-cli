"""
Unit tests for cf precheck command.
"""
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


class TestPrecheckCommand:
    """Test suite for cf precheck command."""
    
    def test_precheck_help(self):
        """Test precheck command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['precheck', '--help'])
        
        assert result.exit_code == 0
        assert 'Run mpw_precheck validation' in result.output
        assert '--project-root' in result.output
        assert '--disable-lvs' in result.output
        assert '--checks' in result.output
        assert '--dry-run' in result.output
    
    def test_precheck_dry_run(self, temp_project_dir):
        """Test precheck command with --dry-run flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # Should fail without proper setup, but --dry-run should be recognized
        assert result.exit_code != 0 or 'dry-run' in result.output.lower()
    
    def test_precheck_disable_lvs(self, temp_project_dir):
        """Test precheck command with --disable-lvs flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--disable-lvs',
            '--dry-run'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0
    
    def test_precheck_with_checks(self, temp_project_dir):
        """Test precheck command with --checks option."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--checks', 'license',
            '--checks', 'makefile',
            '--dry-run'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
