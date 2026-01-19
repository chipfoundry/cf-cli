"""
Unit tests for cf confirm command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main


class TestConfirmCommand:
    """Test suite for cf confirm command."""
    
    def test_confirm_help(self):
        """Test confirm command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['confirm', '--help'])
        
        assert result.exit_code == 0
        assert 'Confirm project submission' in result.output
        assert '--project-root' in result.output
        assert '--sftp-host' in result.output
        assert '--sftp-username' in result.output
        assert '--sftp-key' in result.output
        assert '--project-name' in result.output
    
    def test_confirm_with_all_options(self):
        """Test confirm command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'confirm',
            '--project-root', '/tmp/test',
            '--sftp-host', 'test.example.com',
            '--sftp-username', 'testuser',
            '--project-name', 'test_project'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
