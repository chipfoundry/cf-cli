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
        assert 'Run precheck validation' in result.output
        assert '--project-root' in result.output
        assert '--skip-checks' in result.output
        assert '--magic-drc' in result.output
        assert '--checks' in result.output
        assert '--dry-run' in result.output
        assert '--poll' in result.output
        assert '--wait-timeout' in result.output
        assert '--list-checks' in result.output
        assert 'Available checks' in result.output
        assert 'topcell_check' in result.output
        assert 'lvs' in result.output

    def test_precheck_list_checks(self):
        """--list-checks prints all known check refs and exits cleanly."""
        from chipfoundry_cli.check_refs import PRECHECK_CHECKS

        runner = CliRunner()
        result = runner.invoke(main, ['precheck', '--list-checks'])

        assert result.exit_code == 0
        for check in PRECHECK_CHECKS:
            assert check.ref in result.output
        assert 'opt-in' in result.output
    
    def test_precheck_dry_run(self, temp_project_dir):
        """Test precheck command with --dry-run flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])
    
    def test_precheck_skip_checks(self, temp_project_dir):
        """Test precheck command with --skip-checks flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--skip-checks', 'lvs',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])
    
    def test_precheck_with_checks(self, temp_project_dir):
        """Test precheck command with --checks option."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--checks', 'topcell_check',
            '--checks', 'gpio_defines',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])
    
    def test_precheck_magic_drc(self, temp_project_dir):
        """Test precheck command with --magic-drc flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--magic-drc',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert any(keyword in result.output.lower() for keyword in ['precheck', 'pdk', 'setup', 'dry'])


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
