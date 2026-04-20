"""
Unit tests for config, keygen, and keyview commands.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from pathlib import Path
import os
import tempfile
import shutil


class TestConfigCommand:
    """Test suite for cf config command."""
    
    def test_config_help(self):
        """Test config command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['config', '--help'])
        
        assert result.exit_code == 0
        assert 'Configure a custom SSH private key path for SFTP access' in result.output


class TestKeygenCommand:
    """Test suite for cf keygen command."""
    
    def test_keygen_help(self):
        """Test keygen command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['keygen', '--help'])
        
        assert result.exit_code == 0
        assert 'Generate SSH key' in result.output
    
    def test_keygen_overwrite_flag(self):
        """Test keygen command with --overwrite flag."""
        runner = CliRunner()
        result = runner.invoke(main, ['keygen', '--help'])
        
        assert result.exit_code == 0
        assert '--overwrite' in result.output


class TestKeyviewCommand:
    """Test suite for cf keyview command."""
    
    def test_keyview_help(self):
        """Test keyview command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['keyview', '--help'])
        
        assert result.exit_code == 0
        assert 'Display the current ChipFoundry SSH key' in result.output


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
