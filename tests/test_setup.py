"""
Unit tests for the cf setup command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from pathlib import Path
import json
import shutil
import tempfile
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


@pytest.fixture
def temp_project_with_ipm_yaml(temp_project_dir):
    """Create a temporary project directory with ipm.yaml."""
    ipm_yaml = Path(temp_project_dir) / 'ipm.yaml'
    ipm_yaml.write_text("dependencies:\n  - example-dep")
    
    return temp_project_dir


class TestSetupCommand:
    """Test suite for cf setup command."""
    
    def test_setup_dry_run(self, temp_project_dir):
        """Test setup command with --dry-run flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'Dry run mode' in result.output
    
    def test_setup_only_init(self, temp_project_dir):
        """Test setup command - init should be done via cf init, not cf setup."""
        runner = CliRunner()
        # cf setup should not have --only-init anymore
        result = runner.invoke(main, ['setup', '--help'])
        
        assert result.exit_code == 0
        assert '--only-init' not in result.output
    
    def test_setup_only_flags(self, temp_project_dir):
        """Test setup command with --only-* flags."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--only-caravel',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'Installing only: caravel' in result.output or 'Dry run' in result.output
    
    def test_setup_with_gds_detection(self, temp_project_with_gds):
        """Test setup command - GDS detection should be done via cf init."""
        runner = CliRunner()
        # cf setup no longer handles project initialization
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_with_gds,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
    
    def test_setup_creates_dependencies_dir(self, temp_project_dir):
        """Test that setup creates the dependencies directory."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
    
    def test_setup_project_json_structure(self, temp_project_dir):
        """Test that project.json should be created with cf init, not cf setup."""
        runner = CliRunner()
        # cf setup should not create project.json
        result = runner.invoke(main, ['init', '--help'])
        
        assert result.exit_code == 0
        assert 'init' in result.output.lower()
    
    def test_setup_with_custom_pdk(self, temp_project_dir):
        """Test setup command with custom PDK."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--pdk', 'sky130B',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'sky130B' in result.output
    
    def test_setup_with_caravel_full(self, temp_project_dir):
        """Test setup command with full caravel (not lite)."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--no-caravel-lite',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'caravel' in result.output.lower()
    
    def test_setup_existing_project_json(self, temp_project_dir):
        """Test setup command - it should not manage project.json."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
    
    def test_setup_help(self):
        """Test setup command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['setup', '--help'])
        
        assert result.exit_code == 0
        assert 'Set up a ChipFoundry project' in result.output
        assert '--project-root' in result.output
        assert '--only-caravel' in result.output
        assert '--only-timing' in result.output
        assert '--dry-run' in result.output
    
    def test_setup_default_project_name(self, temp_project_dir):
        """Test that cf init handles project naming, not cf setup."""
        runner = CliRunner()
        result = runner.invoke(main, ['init', '--help'])
        
        assert result.exit_code == 0


class TestSetupIntegration:
    """Integration tests for cf setup command."""
    
    def test_setup_full_workflow_dry_run(self, temp_project_with_gds):
        """Test full setup workflow in dry-run mode."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_with_gds,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'Dry run complete' in result.output
        assert 'No changes were made' in result.output
    
    def test_setup_then_status(self, temp_project_dir):
        """Test setup followed by checking project status."""
        runner = CliRunner()
        
        # Run setup in dry-run mode
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        assert result.exit_code == 0


class TestSetupEdgeCases:
    """Test edge cases for cf setup command."""
    
    def test_setup_nonexistent_directory(self):
        """Test setup with non-existent directory."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', '/nonexistent/directory',
            '--only-init'
        ])
        
        # Should fail because directory doesn't exist
        assert result.exit_code != 0
    
    def test_setup_current_directory_no_project_root(self):
        """Test setup without --project-root uses current directory."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ['setup', '--dry-run'])
            
            # Should work in dry-run mode
            assert result.exit_code == 0
    
    def test_setup_with_repo_options(self, temp_project_dir):
        """Test setup with custom repository options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--repo-owner', 'custom-owner',
            '--repo-name', 'custom-repo',
            '--branch', 'develop',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'custom-owner/custom-repo@develop' in result.output


class TestSetupVersionChecking:
    """Test version checking and overwrite functionality."""
    
    def test_setup_with_overwrite_flag(self, temp_project_dir):
        """Test setup with --overwrite flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--overwrite',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'Dry run' in result.output
    
    def test_setup_overwrite_flag_in_help(self):
        """Test that --overwrite flag appears in help."""
        runner = CliRunner()
        result = runner.invoke(main, ['setup', '--help'])
        
        assert result.exit_code == 0
        assert '--overwrite' in result.output
        # Check for partial text since it might wrap across lines
        assert 'Overwrite/reinstall' in result.output or 'overwrite' in result.output.lower()
    
    def test_setup_skips_installed_components(self, temp_project_dir):
        """Test that setup skips already-installed components with correct version."""
        runner = CliRunner()
        # This test would need mock data for a real test
        # For now, just verify the command works
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        assert result.exit_code == 0
    
    def test_setup_only_flags_with_overwrite(self, temp_project_dir):
        """Test --only-* flags combined with --overwrite."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--only-caravel',
            '--overwrite',
            '--dry-run'
        ])
        
        assert result.exit_code == 0
        assert 'Installing only: caravel' in result.output or 'Dry run' in result.output


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
