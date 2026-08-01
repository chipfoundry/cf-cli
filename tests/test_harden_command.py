"""
Unit tests for cf harden command.
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


class TestHardenCommand:
    """Test suite for cf harden command."""
    
    def test_harden_help(self):
        """Test harden command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['harden', '--help'])
        
        assert result.exit_code == 0
        assert 'Harden a macro using LibreLane' in result.output
        assert '--project-root' in result.output
        assert '--list' in result.output
        assert '--list-from-steps' in result.output
        assert '--tag' in result.output
        assert '--from' in result.output
        assert '--open-in-openroad' in result.output
        assert '--open-in-klayout' in result.output
        assert '--pdk' in result.output
        assert '--use-nix' in result.output
        assert '--use-docker' in result.output
    
    def test_harden_list(self, temp_project_dir):
        """Test harden command with --list flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'harden',
            '--project-root', temp_project_dir,
            '--list'
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'openlane' in result.output.lower() or 'macro' in result.output.lower()
    
    def test_harden_with_macro(self, temp_project_dir):
        """Test harden command with macro argument."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'harden',
            'user_proj_example',
            '--project-root', temp_project_dir,
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'openlane' in result.output.lower() or 'macro' in result.output.lower()
    
    def test_harden_with_all_options(self, temp_project_dir):
        """Test harden command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'harden',
            'user_proj_example',
            '--project-root', temp_project_dir,
            '--tag', 'test_tag',
            '--from', 'OpenROAD.DetailedRouting',
            '--pdk', 'sky130A',
            '--use-docker',
        ])
        
        # Command returns 0 even on error, just prints error message
        assert result.exit_code == 0
        assert 'openlane' in result.output.lower() or 'macro' in result.output.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
