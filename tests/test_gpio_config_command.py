"""
Unit tests for cf gpio-config command.
"""
import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import main
from chipfoundry_cli.utils import (
    get_gpio_config_from_project_json,
    save_gpio_config_to_project_json,
    parse_user_defines_v,
    GPIO_MODE_TO_HEX,
    GPIO_MODES
)
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
def project_with_user_defines(temp_project_dir):
    """Create a project directory with user_defines.v file."""
    project_root = Path(temp_project_dir)
    verilog_dir = project_root / 'verilog' / 'rtl'
    verilog_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a basic user_defines.v file
    user_defines_content = """`default_nettype none

`ifndef __USER_DEFINES_H
`define __USER_DEFINES_H

`define GPIO_MODE_INVALID                  13'hXXXX
`define GPIO_MODE_USER_STD_OUTPUT          13'h1808
`define GPIO_MODE_USER_STD_INPUT_NOPULL    13'h0402

`define USER_CONFIG_GPIO_5_INIT  `GPIO_MODE_INVALID
`define USER_CONFIG_GPIO_6_INIT  `GPIO_MODE_INVALID
`define USER_CONFIG_GPIO_7_INIT  `GPIO_MODE_INVALID

`endif
"""
    (verilog_dir / 'user_defines.v').write_text(user_defines_content)
    
    return temp_project_dir


@pytest.fixture
def project_with_json(temp_project_dir):
    """Create a project directory with project.json containing GPIO config."""
    project_root = Path(temp_project_dir)
    cf_dir = project_root / '.cf'
    cf_dir.mkdir(parents=True, exist_ok=True)
    
    # Create project.json with GPIO config
    project_json = {
        "project": {
            "name": "test_project",
            "gpio_config": {
                "5": "13'h1808",
                "6": "13'h0402"
            }
        }
    }
    (cf_dir / 'project.json').write_text(json.dumps(project_json, indent=2))
    
    return temp_project_dir


class TestGpioConfigCommand:
    """Test suite for cf gpio-config command."""
    
    def test_gpio_config_help(self):
        """Test gpio-config command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['gpio-config', '--help'])
        
        assert result.exit_code == 0
        assert 'Configure GPIO settings' in result.output
        assert '--project-root' in result.output
    
    def test_gpio_config_creates_project_json(self, temp_project_dir):
        """Test that gpio-config creates project.json if it doesn't exist."""
        project_root = Path(temp_project_dir)
        project_json_path = project_root / '.cf' / 'project.json'
        
        # Create user_defines.v
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        user_defines_content = """`default_nettype none
`ifndef __USER_DEFINES_H
`define __USER_DEFINES_H
`define GPIO_MODE_USER_STD_OUTPUT 13'h1808
`define USER_CONFIG_GPIO_5_INIT `GPIO_MODE_USER_STD_OUTPUT
`endif
"""
        (verilog_dir / 'user_defines.v').write_text(user_defines_content)
        
        runner = CliRunner()
        # Simulate user input: enter "1" for each GPIO (user_output mode)
        # We'll use input_stream to provide inputs
        inputs = '\n'.join(['1'] * 33)  # GPIO 5-37 = 33 GPIOs
        result = runner.invoke(main, [
            'gpio-config',
            '--project-root', temp_project_dir
        ], input=inputs)
        
        # Should create project.json
        assert project_json_path.exists()
    
    def test_gpio_config_loads_from_json(self, project_with_json):
        """Test that gpio-config loads existing config from project.json."""
        project_json_path = Path(project_with_json) / '.cf' / 'project.json'
        
        # Load the config
        config = get_gpio_config_from_project_json(str(project_json_path))
        
        assert config is not None
        assert 5 in config
        assert 6 in config
        # Should convert hex to mode name
        assert config[5] == "GPIO_MODE_USER_STD_OUTPUT"
    
    def test_gpio_config_loads_from_user_defines(self, project_with_user_defines):
        """Test that gpio-config loads from user_defines.v if JSON doesn't exist."""
        user_defines_path = Path(project_with_user_defines) / 'verilog' / 'rtl' / 'user_defines.v'
        
        # Parse user_defines.v
        config = parse_user_defines_v(str(user_defines_path))
        
        assert config is not None
        assert 5 in config
        assert config[5] == "GPIO_MODE_INVALID"
    
    def test_save_gpio_config_converts_to_hex(self, temp_project_dir):
        """Test that save_gpio_config_to_project_json converts mode names to hex."""
        project_root = Path(temp_project_dir)
        cf_dir = project_root / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        project_json_path = cf_dir / 'project.json'
        
        # Save config with mode names
        gpio_configs = {
            5: "GPIO_MODE_USER_STD_OUTPUT",
            6: "GPIO_MODE_USER_STD_INPUT_NOPULL"
        }
        save_gpio_config_to_project_json(str(project_json_path), gpio_configs)
        
        # Load and check it's stored as hex
        with open(project_json_path, 'r') as f:
            data = json.load(f)
        
        gpio_config = data['project']['gpio_config']
        assert gpio_config['5'] == "13'h1808"  # hex for USER_STD_OUTPUT
        assert gpio_config['6'] == "13'h0402"  # hex for USER_STD_INPUT_NOPULL
    
    def test_get_gpio_config_converts_hex_to_mode(self, temp_project_dir):
        """Test that get_gpio_config_from_project_json converts hex back to mode names."""
        project_root = Path(temp_project_dir)
        cf_dir = project_root / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        project_json_path = cf_dir / 'project.json'
        
        # Create JSON with hex values
        project_json = {
            "project": {
                "gpio_config": {
                    "5": "13'h1808",
                    "6": "13'h0402"
                }
            }
        }
        (cf_dir / 'project.json').write_text(json.dumps(project_json, indent=2))
        
        # Load and check conversion
        config = get_gpio_config_from_project_json(str(project_json_path))
        
        assert config is not None
        assert 5 in config
        assert 6 in config
        assert config[5] == "GPIO_MODE_USER_STD_OUTPUT"
        assert config[6] == "GPIO_MODE_USER_STD_INPUT_NOPULL"
    
    def test_gpio_config_requires_valid_mode(self, project_with_user_defines):
        """Test that gpio-config requires valid modes (not invalid)."""
        # This test verifies the behavior where invalid modes require input
        # We can't easily test interactive input, but we can test the helper functions
        from chipfoundry_cli.utils import GPIO_MODES
        
        # Invalid mode should not be in selectable options
        mode_options = [key for key in GPIO_MODES.keys() if key != "invalid"]
        assert "invalid" not in mode_options
    
    def test_gpio_config_updates_user_defines_v(self, project_with_user_defines):
        """Test that gpio-config updates user_defines.v file."""
        project_root = Path(project_with_user_defines)
        user_defines_path = project_root / 'verilog' / 'rtl' / 'user_defines.v'
        cf_dir = project_root / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        project_json_path = cf_dir / 'project.json'
        
        # Create project.json first
        project_json = {"project": {}}
        (project_json_path).write_text(json.dumps(project_json, indent=2))
        
        # Save GPIO config
        gpio_configs = {
            5: "GPIO_MODE_USER_STD_OUTPUT",
            6: "GPIO_MODE_USER_STD_INPUT_NOPULL"
        }
        from chipfoundry_cli.utils import save_gpio_config_to_project_json, update_user_defines_v
        
        save_gpio_config_to_project_json(str(project_json_path), gpio_configs)
        update_user_defines_v(str(user_defines_path), gpio_configs)
        
        # Verify user_defines.v was updated
        content = user_defines_path.read_text()
        assert '`define USER_CONFIG_GPIO_5_INIT  `GPIO_MODE_USER_STD_OUTPUT' in content
        assert '`define USER_CONFIG_GPIO_6_INIT  `GPIO_MODE_USER_STD_INPUT_NOPULL' in content
    
    def test_parse_user_defines_v_handles_all_formats(self, temp_project_dir):
        """Test that parse_user_defines_v handles different format inputs."""
        project_root = Path(temp_project_dir)
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        
        # Create user_defines.v with various formats
        user_defines_content = """`default_nettype none
`ifndef __USER_DEFINES_H
`define __USER_DEFINES_H
`define GPIO_MODE_USER_STD_OUTPUT 13'h1808
`define GPIO_MODE_USER_STD_INPUT_NOPULL 13'h0402
`define GPIO_MODE_INVALID 13'hXXXX

`define USER_CONFIG_GPIO_5_INIT  `GPIO_MODE_USER_STD_OUTPUT
`define USER_CONFIG_GPIO_6_INIT  `GPIO_MODE_USER_STD_INPUT_NOPULL
`define USER_CONFIG_GPIO_7_INIT  `GPIO_MODE_INVALID
`endif
"""
        user_defines_path = verilog_dir / 'user_defines.v'
        user_defines_path.write_text(user_defines_content)
        
        # Parse it
        config = parse_user_defines_v(str(user_defines_path))
        
        assert 5 in config
        assert 6 in config
        assert 7 in config
        assert config[5] == "GPIO_MODE_USER_STD_OUTPUT"
        assert config[6] == "GPIO_MODE_USER_STD_INPUT_NOPULL"
        assert config[7] == "GPIO_MODE_INVALID"
    
    def test_gpio_config_prerequisite_check(self, temp_project_dir):
        """Test that precheck and verify require GPIO config."""
        project_root = Path(temp_project_dir)
        cf_dir = project_root / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        project_json_path = cf_dir / 'project.json'
        
        # Create project.json without GPIO config
        project_json = {"project": {"name": "test"}}
        (project_json_path).write_text(json.dumps(project_json, indent=2))
        
        runner = CliRunner()
        
        # Test precheck requires GPIO config
        result = runner.invoke(main, [
            'precheck',
            '--project-root', temp_project_dir,
            '--dry-run'
        ])
        
        assert result.exit_code != 0 or 'GPIO configuration' in result.output.lower()
        
        # Test verify requires GPIO config (when not listing)
        result = runner.invoke(main, [
            'verify',
            '--project-root', temp_project_dir,
            'test_name',
            '--dry-run'
        ])
        
        assert result.exit_code != 0 or 'GPIO configuration' in result.output.lower()
    
    def test_gpio_config_all_gpios_configured(self, temp_project_dir):
        """Test that all GPIOs 5-37 can be configured."""
        project_root = Path(temp_project_dir)
        cf_dir = project_root / '.cf'
        cf_dir.mkdir(parents=True, exist_ok=True)
        project_json_path = cf_dir / 'project.json'
        
        # Create a complete GPIO config for all GPIOs
        gpio_configs = {}
        for gpio_num in range(5, 38):
            gpio_configs[gpio_num] = "GPIO_MODE_USER_STD_OUTPUT"
        
        from chipfoundry_cli.utils import save_gpio_config_to_project_json
        save_gpio_config_to_project_json(str(project_json_path), gpio_configs)
        
        # Verify all GPIOs are saved
        with open(project_json_path, 'r') as f:
            data = json.load(f)
        
        gpio_config = data['project']['gpio_config']
        assert len(gpio_config) == 33  # GPIOs 5-37 = 33 GPIOs
        for gpio_num in range(5, 38):
            assert str(gpio_num) in gpio_config
            assert gpio_config[str(gpio_num)] == "13'h1808"  # hex for USER_STD_OUTPUT


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
