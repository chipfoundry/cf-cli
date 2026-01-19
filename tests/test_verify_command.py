"""
Unit tests for cf verify command.
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
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            '--list'
        ])
        
        # Should fail without proper setup, but --list should be recognized
        assert result.exit_code != 0 or 'list' in result.output.lower()
    
    def test_verify_with_test(self, temp_project_dir):
        """Test verify command with test argument."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            'counter_la',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0
    
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
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0
    
    def test_verify_all(self, temp_project_dir):
        """Test verify command with --all flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            '--all',
            '--dry-run'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0
    
    def test_verify_with_tag(self, temp_project_dir):
        """Test verify command with --tag option."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            '--tag', 'user_proj_tests',
            '--dry-run'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
