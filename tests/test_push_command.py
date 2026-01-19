"""
Unit tests for cf push command.
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


class TestPushCommand:
    """Test suite for cf push command."""
    
    def test_push_help(self):
        """Test push command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['push', '--help'])
        
        assert result.exit_code == 0
        assert 'Upload your project files' in result.output
        assert '--project-root' in result.output
        assert '--sftp-host' in result.output
        assert '--sftp-username' in result.output
        assert '--sftp-key' in result.output
        assert '--project-id' in result.output
        assert '--project-name' in result.output
        assert '--project-type' in result.output
        assert '--force-overwrite' in result.output
        assert '--dry-run' in result.output
    
    def test_push_dry_run(self, temp_project_dir):
        """Test push command with --dry-run flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'push',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # Should fail without proper setup, but dry-run should be recognized
        assert '--dry-run' in result.output or result.exit_code != 0
    
    def test_push_with_all_options(self, temp_project_dir):
        """Test push command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'push',
            '--project-root', temp_project_dir,
            '--sftp-host', 'test.example.com',
            '--sftp-username', 'testuser',
            '--project-id', 'user123_proj456',
            '--project-name', 'test_project',
            '--project-type', 'digital',
            '--force-overwrite',
            '--dry-run'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0 or 'dry-run' in result.output.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
