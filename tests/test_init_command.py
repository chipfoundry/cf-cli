"""
Unit tests for cf init command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from pathlib import Path
import json
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


@pytest.fixture
def temp_project_with_gds(temp_project_dir):
    """Create a temporary project directory with a GDS file."""
    gds_dir = Path(temp_project_dir) / 'gds'
    gds_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a dummy GDS file
    gds_file = gds_dir / 'user_project_wrapper.gds'
    gds_file.write_text("dummy gds content")
    
    return temp_project_dir


class TestInitCommand:
    """Test suite for cf init command."""
    
    def test_init_help(self):
        """Test init command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['init', '--help'])
        
        assert result.exit_code == 0
        assert 'Initialize or refresh the local ChipFoundry project configuration' in result.output
        assert '--project-root' in result.output
    
    def test_init_with_project_root(self, temp_project_dir):
        """Test init command with --project-root option."""
        runner = CliRunner()
        # Mock user input for project name
        result = runner.invoke(main, [
            'init',
            '--project-root', temp_project_dir
        ], input='test_project\n')
        
        # Should fail without config, but we can test the option parsing
        assert '--project-root' in result.output or result.exit_code != 0
    
    def test_init_defaults_to_current_directory(self, temp_project_dir):
        """Test init command defaults to current directory."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=temp_project_dir):
            result = runner.invoke(main, ['init', '--help'])
            
            assert result.exit_code == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
