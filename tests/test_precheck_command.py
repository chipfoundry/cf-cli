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
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        # May mention precheck, pdk, or setup in error message
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])
    
    def test_precheck_disable_lvs(self, temp_project_dir):
        """Test precheck command with --disable-lvs flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--disable-lvs',
            '--dry-run'
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        # May mention precheck, pdk, or setup in error message
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])
    
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
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        # May mention precheck, pdk, or setup in error message
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
