"""
Unit tests for cf pull command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main


class TestPullCommand:
    """Test suite for cf pull command."""
    
    def test_pull_help(self):
        """Test pull command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['pull', '--help'])
        
        assert result.exit_code == 0
        assert 'Download results/artifacts' in result.output
        assert '--project-name' in result.output
        assert '--output-dir' in result.output
        assert '--sftp-host' in result.output
        assert '--sftp-username' in result.output
        assert '--sftp-key' in result.output
    
    def test_pull_with_all_options(self):
        """Test pull command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'pull',
            '--project-name', 'test_project',
            '--output-dir', '/tmp/output',
            '--sftp-host', 'test.example.com',
            '--sftp-username', 'testuser'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
