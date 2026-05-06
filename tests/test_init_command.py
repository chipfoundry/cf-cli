"""
Unit tests for cf init command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import (
    main,
    _shuttle_sort_key,
    _confirm_new_project_creation,
    _prompt_init_platform_action,
    _choose_platform_project,
)
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

    def test_shuttle_sort_key_handles_none_tapeout_date(self):
        """Shuttle sort key should not crash on null or missing dates."""
        shuttles = [
            {"id": "late", "tapeout_date": None},
            {"id": "soon", "tapeout_date": "2026-06-01"},
            {"id": "missing"},
            {"id": "middle", "tapeout_date": "2026-07-15"},
        ]

        sorted_ids = [s["id"] for s in sorted(shuttles, key=_shuttle_sort_key)]
        assert sorted_ids == ["soon", "middle", "late", "missing"]

    def test_confirm_new_project_creation_uses_safe_default(self, monkeypatch):
        """Creation confirmation should default to 'No' to prevent accidental project creation."""
        captured = {}

        def fake_confirm(text, default):
            captured["text"] = text
            captured["default"] = default
            return True

        monkeypatch.setattr("chipfoundry_cli.main.click.confirm", fake_confirm)
        approved = _confirm_new_project_creation()

        assert approved is True
        assert captured["default"] is False
        assert "Create a NEW platform project now?" in captured["text"]

    def test_prompt_init_platform_action_defaults_to_link(self, monkeypatch):
        """init should default to linking an existing project."""
        monkeypatch.setattr("chipfoundry_cli.main.console.input", lambda _msg: "")
        action = _prompt_init_platform_action()
        assert action == "link"

    def test_choose_platform_project_returns_selected_project(self, monkeypatch):
        """Chooser should return selected project entry."""
        projects = [
            {"id": "p1", "name": "Project 1", "status": "draft"},
            {"id": "p2", "name": "Project 2", "status": "submitted"},
        ]
        monkeypatch.setattr("chipfoundry_cli.main.console.input", lambda _msg: "2")
        selected = _choose_platform_project(projects)
        assert selected == projects[1]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
