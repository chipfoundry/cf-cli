"""
Unit tests for cf status command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main


class TestStatusCommand:
    """Test suite for cf status command."""
    
    def test_status_help(self):
        """Test status command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['status', '--help'])
        
        assert result.exit_code == 0
        assert 'Show all projects and outputs' in result.output
        assert '--sftp-host' in result.output
        assert '--sftp-username' in result.output
        assert '--sftp-key' in result.output
    
    def test_status_with_all_options(self):
        """Test status command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'status',
            '--sftp-host', 'test.example.com',
            '--sftp-username', 'testuser'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
