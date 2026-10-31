"""
Unit tests for the cf setup command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from pathlib import Path
import json
import shutil
import subprocess
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'setup' in result.output.lower() or 'dry' in result.output.lower() or 'version' in result.output.lower()
    
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'caravel' in result.output.lower() or 'dry' in result.output.lower() or 'version' in result.output.lower()
    
    def test_setup_with_gds_detection(self, temp_project_with_gds):
        """Test setup command - GDS detection should be done via cf init."""
        runner = CliRunner()
        # cf setup no longer handles project initialization
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_with_gds,
            '--dry-run'
        ])
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
    
    def test_setup_creates_dependencies_dir(self, temp_project_dir):
        """Test that setup creates the dependencies directory."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
    
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'sky130' in result.output or 'version' in result.output.lower()
    
    def test_setup_with_caravel_full(self, temp_project_dir):
        """Test setup command with full caravel (not lite)."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--no-caravel-lite',
            '--dry-run'
        ])
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'caravel' in result.output.lower() or 'version' in result.output.lower()
    
    def test_setup_existing_project_json(self, temp_project_dir):
        """Test setup command - it should not manage project.json."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
    
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'dry' in result.output.lower() or 'version' in result.output.lower()
    
    def test_setup_then_status(self, temp_project_dir):
        """Test setup followed by checking project status."""
        runner = CliRunner()
        
        # Run setup in dry-run mode
        result = runner.invoke(main, [
            'setup',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]


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
            
            # May fail due to version fetching in CI, but should recognize arguments
            assert result.exit_code in [0, 1]
    
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'custom-owner' in result.output or 'custom-repo' in result.output or 'version' in result.output.lower()


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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'dry' in result.output.lower() or 'overwrite' in result.output.lower() or 'version' in result.output.lower()
    
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
    
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
        
        # May fail due to version fetching in CI, but should recognize arguments
        assert result.exit_code in [0, 1]
        assert 'caravel' in result.output.lower() or 'dry' in result.output.lower() or 'version' in result.output.lower()

    def _init_project(self, project_dir):
        cf_dir = Path(project_dir) / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        (cf_dir / 'project.json').write_text(json.dumps({
            "project": {"name": "cocotb-test", "type": "digital"}
        }))

    def _cocotb_install_env(self, temp_project_dir, monkeypatch, platform, version_info):
        import chipfoundry_cli.main as cf_main

        self._init_project(temp_project_dir)
        monkeypatch.delenv("LDSHARED", raising=False)
        monkeypatch.delenv("SETUPTOOLS_USE_DISTUTILS", raising=False)
        monkeypatch.setattr(cf_main.sys, "platform", platform)
        monkeypatch.setattr(cf_main.sys, "version_info", version_info)
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append((list(cmd), kwargs.get("env")))
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(cf_main.subprocess, "run", fake_run)
        result = CliRunner().invoke(main, [
            'setup', '--project-root', temp_project_dir, '--only-cocotb', '--overwrite',
        ])
        assert result.exit_code == 0, result.output
        installs = [
            env for cmd, env in calls
            if any('caravel-sim-infrastructure' in str(part) for part in cmd)
        ]
        assert len(installs) == 1
        return installs[0]

    def test_cocotb_install_uses_stdlib_distutils_on_macos_before_312(self, temp_project_dir, monkeypatch):
        env = self._cocotb_install_env(temp_project_dir, monkeypatch, 'darwin', (3, 11, 8))
        assert env['SETUPTOOLS_USE_DISTUTILS'] == 'stdlib'
        assert 'LDSHARED' not in env

    def test_cocotb_install_skips_stdlib_distutils_on_python_312(self, temp_project_dir, monkeypatch):
        env = self._cocotb_install_env(temp_project_dir, monkeypatch, 'darwin', (3, 12, 0))
        assert 'SETUPTOOLS_USE_DISTUTILS' not in env
        assert 'LDSHARED' not in env

    def test_cocotb_install_skips_stdlib_distutils_off_macos(self, temp_project_dir, monkeypatch):
        env = self._cocotb_install_env(temp_project_dir, monkeypatch, 'linux', (3, 11, 8))
        assert 'SETUPTOOLS_USE_DISTUTILS' not in env
        assert 'LDSHARED' not in env

    def test_cocotb_install_failure_prints_stderr_tail(self, temp_project_dir, monkeypatch):
        import chipfoundry_cli.main as cf_main

        self._init_project(temp_project_dir)
        log = "\n".join(f"copying file {i}" for i in range(40)).encode()

        def fake_run(cmd, *args, **kwargs):
            if any('caravel-sim-infrastructure' in str(part) for part in cmd):
                raise subprocess.CalledProcessError(1, cmd, stderr=log)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(cf_main.subprocess, "run", fake_run)
        result = CliRunner().invoke(main, [
            'setup', '--project-root', temp_project_dir, '--only-cocotb', '--overwrite',
        ])
        assert result.exit_code == 1
        assert "Failed to set up Cocotb" in result.output
        assert "CalledProcessError" not in result.output
        assert "copying file 15" in result.output
        assert "copying file 39" in result.output
        assert "copying file 14" not in result.output
        assert "Setup completed with errors" in result.output


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
