"""
Unit tests for cf precheck command and infrastructure.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from pathlib import Path
import tempfile
import shutil
import os
import json


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def initialized_project_dir(temp_project_dir):
    """Create an initialized project directory with .cf/project.json."""
    cf_dir = Path(temp_project_dir) / '.cf'
    cf_dir.mkdir(parents=True, exist_ok=True)
    
    project_json = {
        "project": {
            "name": "test_project",
            "type": "digital"
        },
        "pdk": "sky130A",
        "gpio_config": {str(i): "0x1800" for i in range(5, 38)}
    }
    
    with open(cf_dir / 'project.json', 'w') as f:
        json.dump(project_json, f)
    
    # Create dependencies/pdks directory structure
    pdk_path = Path(temp_project_dir) / 'dependencies' / 'pdks' / 'sky130A'
    pdk_path.mkdir(parents=True, exist_ok=True)
    
    yield temp_project_dir


class TestPrecheckCommand:
    """Test suite for cf precheck command."""
    
    def test_precheck_help(self):
        """Test precheck command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['precheck', '--help'])
        
        assert result.exit_code == 0
        assert 'Run precheck validation' in result.output
        assert '--project-root' in result.output
        assert '--check' in result.output
        assert '--skip' in result.output
        assert '--private' in result.output
        assert '--list' in result.output
        assert '--dry-run' in result.output
        assert '--verbose' in result.output
    
    def test_precheck_list_checks(self):
        """Test precheck --list command."""
        runner = CliRunner()
        result = runner.invoke(main, ['precheck', '--list'])
        
        assert result.exit_code == 0
        # Should list available checks
        assert 'Available Precheck Checks' in result.output
        assert 'gpio_defines' in result.output
        assert 'lvs' in result.output
    
    def test_precheck_dry_run_uninitialized(self, temp_project_dir):
        """Test precheck --dry-run on uninitialized project."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        # Should return 0 but show error about uninitialized project
        assert result.exit_code == 0
        assert 'not initialized' in result.output.lower() or 'init' in result.output.lower()
    
    def test_precheck_with_skip(self, temp_project_dir):
        """Test precheck command with --skip option."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--skip', 'lvs',
            '--skip', 'magic_drc',
            '--dry-run'
        ])
        
        # Command should handle skip options
        assert result.exit_code == 0
    
    def test_precheck_private(self, temp_project_dir):
        """Test precheck command with --private flag."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--private',
            '--dry-run'
        ])
        
        # Command should handle private option (uninitialized project still returns 0)
        assert result.exit_code == 0


class TestPrecheckInfrastructure:
    """Test suite for precheck infrastructure components."""
    
    def test_check_registry_import(self):
        """Test that CheckRegistry can be imported."""
        from chipfoundry_cli.precheck.core.registry import CheckRegistry, get_registry
        
        registry = get_registry()
        assert registry is not None
        assert isinstance(registry, CheckRegistry)
    
    def test_check_registry_auto_discover(self):
        """Test that checks can be auto-discovered."""
        from chipfoundry_cli.precheck.core.registry import get_registry
        
        registry = get_registry()
        registry.auto_discover()
        
        # Should have discovered some checks
        all_checks = registry.get_all()
        assert len(all_checks) > 0
    
    def test_check_registry_get_checks(self):
        """Test filtering checks by criteria."""
        from chipfoundry_cli.precheck.core.registry import get_registry
        
        registry = get_registry()
        registry.auto_discover()
        
        # Get native checks only
        native_checks = registry.get_checks(native_only=True)
        for check_class in native_checks:
            assert not check_class.requires_docker
    
    def test_check_result_types(self):
        """Test CheckResult and CheckStatus types."""
        from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
        
        result = CheckResult(
            check_name="test_check",
            status=CheckStatus.PASSED,
            message="Test passed"
        )
        
        assert result.passed
        assert not result.failed
        assert result.status == CheckStatus.PASSED
        
        # Test serialization
        data = result.to_dict()
        assert data['check_name'] == "test_check"
        assert data['status'] == "passed"
    
    def test_check_context_creation(self, temp_project_dir):
        """Test CheckContext creation."""
        from chipfoundry_cli.precheck.core.config import CheckContext
        
        context = CheckContext.from_project(
            project_path=Path(temp_project_dir),
            pdk="sky130A",
            pdk_path=Path(temp_project_dir) / "pdk",
            project_type="digital"
        )
        
        assert context.project_path == Path(temp_project_dir)
        assert context.pdk == "sky130A"
        assert context.project_type == "digital"
        assert context.user_module == "user_project_wrapper"
    
    def test_precheck_config(self):
        """Test PrecheckConfig functionality."""
        from chipfoundry_cli.precheck.core.config import PrecheckConfig
        
        config = PrecheckConfig(
            checks=['gpio_defines'],
            skip_checks=['lvs'],
            native_only=True
        )
        
        # Should run gpio_defines (in checks list, native)
        assert config.should_run_check('gpio_defines', 'gpio', False)
        
        # Should not run lvs (in skip list)
        assert not config.should_run_check('lvs', 'lvs', True)
        
        # Should not run magic_drc (not in checks list)
        assert not config.should_run_check('magic_drc', 'drc', True)
    
    def test_base_check_class(self):
        """Test that BaseCheck subclasses work correctly."""
        from chipfoundry_cli.precheck.checks.base import BaseCheck
        from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
        from chipfoundry_cli.precheck.core.config import CheckContext
        
        class MockCheck(BaseCheck):
            name = "mock_check"
            display_name = "Mock Check"
            description = "A mock check for testing"
            category = "test"
            supported_pdks = ['sky130A']
            supported_types = ['digital']
            requires_docker = False
            
            def run(self, context: CheckContext) -> CheckResult:
                return self._make_passed_result("Mock passed")
        
        check = MockCheck()
        assert check.name == "mock_check"
        assert not check.requires_docker
    
    def test_precheck_logger(self, temp_project_dir):
        """Test PrecheckLogger functionality."""
        from rich.console import Console
        from chipfoundry_cli.precheck.core.logger import PrecheckLogger
        from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
        
        console = Console(force_terminal=True)
        logger = PrecheckLogger(console=console)
        
        logger.start_precheck(temp_project_dir, "sky130A", "digital")
        
        result = CheckResult(
            check_name="Test Check",
            status=CheckStatus.PASSED,
            message="Test passed",
            duration_ms=100
        )
        logger.end_check(result)
        
        summary = logger.end_precheck()
        
        assert summary.passed_count == 1
        assert summary.failed_count == 0


class TestNativeChecks:
    """Test suite for native checks."""
    
    def test_gpio_defines_check_import(self):
        """Test that GPIO defines check can be imported."""
        from chipfoundry_cli.precheck.checks.native.gpio_defines import GpioDefinesCheck
        
        check = GpioDefinesCheck()
        assert check.name == "gpio_defines"
        assert not check.requires_docker
    
    def test_pdn_check_import(self):
        """Test that PDN check can be imported."""
        from chipfoundry_cli.precheck.checks.native.pdn import PdnCheck
        
        check = PdnCheck()
        assert check.name == "pdn"
        assert not check.requires_docker
        assert 'gf180mcu' in ''.join(check.supported_pdks).lower()


class TestDockerChecks:
    """Test suite for Docker checks."""
    
    def test_docker_check_imports(self):
        """Test that Docker checks can be imported."""
        from chipfoundry_cli.precheck.checks.docker.klayout_drc import (
            KlayoutFeolCheck, KlayoutBeolCheck, KlayoutOffgridCheck,
            KlayoutDensityCheck, KlayoutPinLabelCheck, KlayoutZeroareaCheck
        )
        from chipfoundry_cli.precheck.checks.docker.magic_drc import MagicDrcCheck
        from chipfoundry_cli.precheck.checks.docker.lvs import LvsCheck
        from chipfoundry_cli.precheck.checks.docker.xor import XorCheck
        from chipfoundry_cli.precheck.checks.docker.oeb import OebCheck
        
        # All Docker checks should require Docker
        assert KlayoutFeolCheck.requires_docker
        assert MagicDrcCheck.requires_docker
        assert LvsCheck.requires_docker
    
    def test_docker_check_metadata(self):
        """Test Docker check metadata."""
        from chipfoundry_cli.precheck.checks.docker.lvs import LvsCheck
        
        assert LvsCheck.name == "lvs"
        assert LvsCheck.category == "lvs"
        assert LvsCheck.requires_docker


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
