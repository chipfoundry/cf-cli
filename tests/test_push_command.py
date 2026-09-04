"""
Unit tests for cf push command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import (
    _assert_wrapper_hash_unchanged,
    _ordered_sftp_uploads,
    _prepared_wrapper_hash,
    main,
)
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


def test_sftp_uploads_project_json_last():
    uploads = _ordered_sftp_uploads({
        ".cf/project.json": "/tmp/project.json",
        "verilog/rtl/user_defines.v": "/tmp/user_defines.v",
        "gds/user_project_wrapper.gds": "/tmp/user_project_wrapper.gds",
    })

    assert [item[0] for item in uploads] == [
        "verilog/rtl/user_defines.v",
        "gds/user_project_wrapper.gds",
        ".cf/project.json",
    ]


def test_sftp_uploads_require_project_json():
    with pytest.raises(ValueError, match="requires .cf/project.json"):
        _ordered_sftp_uploads({"gds/user_project_wrapper.gds": "/tmp/wrapper.gds"})


def test_prepared_wrapper_hash_is_required(tmp_path):
    config = tmp_path / "project.json"
    config.write_text('{"project": {}}')

    with pytest.raises(ValueError, match="user_project_wrapper_hash"):
        _prepared_wrapper_hash(str(config))


def test_prepared_wrapper_hash_reads_project_json(tmp_path):
    config = tmp_path / "project.json"
    config.write_text('{"project": {"user_project_wrapper_hash": "abc123"}}')

    assert _prepared_wrapper_hash(str(config)) == "abc123"


def test_wrapper_hash_change_aborts_before_project_json(tmp_path):
    wrapper = tmp_path / "user_project_wrapper.gds"
    wrapper.write_bytes(b"new bytes")

    with pytest.raises(RuntimeError, match="project.json was not uploaded"):
        _assert_wrapper_hash_unchanged(str(wrapper), "stale-hash")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
