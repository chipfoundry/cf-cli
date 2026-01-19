"""
Unit tests for tapeout-history and view-tapeout-report commands.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main


class TestTapeoutHistoryCommand:
    """Test suite for cf tapeout-history command."""
    
    def test_tapeout_history_help(self):
        """Test tapeout-history command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['tapeout-history', '--help'])
        
        assert result.exit_code == 0
        assert 'Show all tapeout runs' in result.output
        assert '--sftp-host' in result.output
        assert '--sftp-username' in result.output
        assert '--sftp-key' in result.output
        assert '--limit' in result.output
        assert '--days' in result.output
    
    def test_tapeout_history_with_all_options(self):
        """Test tapeout-history command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'tapeout-history',
            '--sftp-host', 'test.example.com',
            '--sftp-username', 'testuser',
            '--limit', '10',
            '--days', '7'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


class TestViewTapeoutReportCommand:
    """Test suite for cf view-tapeout-report command."""
    
    def test_view_tapeout_report_help(self):
        """Test view-tapeout-report command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['view-tapeout-report', '--help'])
        
        assert result.exit_code == 0
        assert 'View the consolidated tapeout report' in result.output
        assert '--project-name' in result.output
        assert '--report-path' in result.output
    
    def test_view_tapeout_report_with_project_name(self):
        """Test view-tapeout-report command with --project-name."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'view-tapeout-report',
            '--project-name', 'test_project'
        ])
        
        # Should fail without proper setup, but option should be recognized
        assert result.exit_code != 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
