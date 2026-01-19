"""
Comprehensive test to verify all CLI commands are accessible and have proper help text.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main


class TestAllCommands:
    """Test suite to verify all commands exist and are accessible."""
    
    # List of all expected commands
    EXPECTED_COMMANDS = [
        'config',
        'keygen',
        'keyview',
        'init',
        'push',
        'pull',
        'status',
        'tapeout-history',
        'view-tapeout-report',
        'confirm',
        'setup',
        'harden',
        'precheck',
        'verify',
    ]
    
    def test_main_help_shows_all_commands(self):
        """Test that main help shows all commands."""
        runner = CliRunner()
        result = runner.invoke(main, ['--help'])
        
        assert result.exit_code == 0
        output = result.output.lower()
        
        # Check that all commands are mentioned in the help
        for cmd in self.EXPECTED_COMMANDS:
            assert cmd in output or cmd.replace('-', '_') in output, f"Command '{cmd}' not found in main help"
    
    def test_all_commands_have_help(self):
        """Test that all commands respond to --help."""
        runner = CliRunner()
        
        for cmd in self.EXPECTED_COMMANDS:
            result = runner.invoke(main, [cmd, '--help'])
            assert result.exit_code == 0, f"Command '{cmd}' failed to show help"
            assert len(result.output) > 0, f"Command '{cmd}' returned empty help"
    
    def test_version_option(self):
        """Test that --version option works."""
        runner = CliRunner()
        result = runner.invoke(main, ['--version'])
        
        # Version should either succeed or show help
        assert result.exit_code == 0 or 'version' in result.output.lower()
    
    def test_invalid_command(self):
        """Test that invalid commands are rejected."""
        runner = CliRunner()
        result = runner.invoke(main, ['invalid-command'])
        
        # Should fail or show error
        assert result.exit_code != 0 or 'invalid' in result.output.lower() or 'unknown' in result.output.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
