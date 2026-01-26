# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""GPIO defines check - validates user_defines.v configuration."""

import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from chipfoundry_cli.precheck.checks.base import BaseCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


# Chosen illegal value for `USER_CONFIG_GPIO_<int>_INIT directives
VAL_ILLEGAL = "13'hXXXX"
VAL_ILLEGAL_CF = VAL_ILLEGAL.casefold()

# Compiled regex patterns
MODREX = re.compile(r"^__gpioModeObserve[0-9]+$")
WIRREX = re.compile(r"^USER_CONFIG_GPIO_([0-9]+)_INIT$")


@register_check
class GpioDefinesCheck(BaseCheck):
    """Validates GPIO configuration in user_defines.v.
    
    This check verifies that all required USER_CONFIG_GPIO_*_INIT
    directives in verilog/rtl/user_defines.v have valid hex values.
    """
    
    name = "gpio_defines"
    display_name = "GPIO Defines"
    description = "Validates GPIO configuration in user_defines.v"
    category = "gpio"
    supported_pdks = ['sky130A', 'sky130B', 'gf180mcuC', 'gf180mcuD']
    supported_types = ['digital', 'analog']
    requires_docker = False
    
    def run(self, context: CheckContext) -> CheckResult:
        """Execute the GPIO defines check.
        
        Args:
            context: Check execution context
            
        Returns:
            CheckResult indicating the check outcome
        """
        user_defines_path = context.get_user_defines_path()
        
        # Check if user_defines.v exists
        if not user_defines_path.exists():
            return self._make_failed_result(
                f"user_defines.v not found at {user_defines_path}"
            )
        
        if not os.access(str(user_defines_path), os.R_OK):
            return self._make_failed_result(
                f"user_defines.v not readable at {user_defines_path}"
            )
        
        # Get the verilog assets path
        assets_path = Path(__file__).parent.parent.parent / 'assets' / 'gpio_verilog'
        pre_v = assets_path / 'gpio_modes_base.v'
        post_v = assets_path / 'gpio_modes_observe.v'
        
        # Check if assets exist
        if not pre_v.exists() or not post_v.exists():
            # If assets don't exist, do a simpler validation
            return self._simple_validation(user_defines_path, context)
        
        # Full validation with pyverilog
        try:
            return self._full_validation(
                user_defines_path, 
                pre_v, 
                post_v, 
                context
            )
        except ImportError:
            # pyverilog not available, fall back to simple validation
            return self._simple_validation(user_defines_path, context)
    
    def _simple_validation(self, user_defines_path: Path, 
                           context: CheckContext) -> CheckResult:
        """Simple regex-based validation when pyverilog is not available.
        
        Args:
            user_defines_path: Path to user_defines.v
            context: Check execution context
            
        Returns:
            CheckResult
        """
        try:
            with open(user_defines_path, 'r') as f:
                content = f.read()
        except Exception as e:
            return self._make_error_result(f"Failed to read user_defines.v: {e}")
        
        # First, extract all macro definitions to resolve references
        macro_defs = {}
        macro_def_pattern = re.compile(
            r'`define\s+(\w+)\s+(13\'[hH][0-9a-fA-F]+|10\'[hH][0-9a-fA-F]+)'
        )
        for match in macro_def_pattern.finditer(content):
            macro_name = match.group(1)
            macro_value = match.group(2)
            macro_defs[macro_name] = macro_value
        
        # Check for GPIO define patterns
        gpio_pattern = re.compile(
            r'`define\s+USER_CONFIG_GPIO_(\d+)_INIT\s+(\S+)'
        )
        
        found_gpios = {}
        for match in gpio_pattern.finditer(content):
            gpio_num = int(match.group(1))
            value = match.group(2)
            found_gpios[gpio_num] = value
        
        # Determine required GPIOs based on project type
        if context.project_type == 'analog':
            # For analog, GPIOs 14-24 are don't care
            required = set(range(5, 38)) - set(range(14, 25))
        else:
            required = set(range(5, 38))
        
        # Check for missing GPIOs
        missing = required - set(found_gpios.keys())
        if missing:
            return self._make_failed_result(
                f"Missing GPIO definitions for: {sorted(missing)}",
                details={'missing_gpios': sorted(missing)}
            )
        
        # Check for invalid values (placeholder or non-hex)
        if 'gf180mcu' in context.pdk:
            legal_pattern = re.compile(r"^10'[hH][0-9a-fA-F]+$")
        else:
            legal_pattern = re.compile(r"^13'[hH][0-9a-fA-F]+$")
        
        invalid = []
        for gpio_num in sorted(required):
            value = found_gpios.get(gpio_num, '')
            
            # Resolve macro references (values starting with `)
            resolved_value = value
            if value.startswith('`'):
                macro_name = value[1:]  # Remove the backtick
                if macro_name in macro_defs:
                    resolved_value = macro_defs[macro_name]
                else:
                    # Macro not defined in this file - mark as invalid
                    invalid.append(f"GPIO_{gpio_num}={value} (undefined macro)")
                    continue
            
            # Check if it's the invalid placeholder
            if resolved_value.casefold() == VAL_ILLEGAL_CF:
                invalid.append(f"GPIO_{gpio_num}={resolved_value} (placeholder)")
            # Check if it matches the legal pattern
            elif not legal_pattern.match(resolved_value):
                invalid.append(f"GPIO_{gpio_num}={value}")
        
        if invalid:
            return self._make_failed_result(
                f"Invalid GPIO values: {', '.join(invalid[:5])}{'...' if len(invalid) > 5 else ''}",
                details={'invalid_gpios': invalid}
            )
        
        return self._make_passed_result(
            f"All {len(required)} GPIO definitions valid",
            details={'gpio_count': len(required)}
        )
    
    def _full_validation(self, user_defines_path: Path,
                         pre_v: Path, post_v: Path,
                         context: CheckContext) -> CheckResult:
        """Full validation using pyverilog parser.
        
        Args:
            user_defines_path: Path to user_defines.v
            pre_v: Path to gpio_modes_base.v asset
            post_v: Path to gpio_modes_observe.v asset
            context: Check execution context
            
        Returns:
            CheckResult
        """
        from pyverilog.vparser.parser import parse, ParseError
        
        # Determine legal pattern based on PDK
        if 'gf180mcu' in context.pdk:
            legal_pattern = re.compile(r"^10'[hH][0-9a-fA-F]+$")
        else:
            legal_pattern = re.compile(r"^13'[hH][0-9a-fA-F]+$")
        
        # Parse the sandwich of files
        file_list = [pre_v, user_defines_path, post_v]
        
        try:
            ast, _ = parse(file_list)
        except ParseError as e:
            return self._make_failed_result(
                f"Verilog parse error: {e}",
                details={'parse_error': str(e)}
            )
        except Exception as e:
            return self._make_error_result(f"Parse failed: {e}")
        
        # Determine required GPIOs based on project type
        if context.project_type == 'analog':
            want = set(range(5, 38)) - set(range(14, 25))
        else:
            want = set(range(5, 38))
        
        valids = {}
        illegals = []
        
        # Search for matching modules
        for d in ast.description.definitions:
            if type(d).__name__ != 'ModuleDef':
                continue
            if not MODREX.match(d.name):
                continue
            
            # Walk items in module
            for i in d.items:
                if type(i).__name__ != 'Decl' or len(i.list) != 2:
                    continue
                
                i0, i1 = i.list[0], i.list[1]
                if type(i0).__name__ != 'Wire' or type(i1).__name__ != 'Assign':
                    continue
                
                match = WIRREX.match(i0.name)
                if not match:
                    continue
                
                windex = int(match.group(1))
                if windex not in want:
                    continue
                
                want.remove(windex)
                
                # Extract value
                try:
                    val = i1.right.var.value
                except AttributeError:
                    try:
                        val = str(i1.right.var)
                    except:
                        val = "<error-unrecognized>"
                
                if val.casefold() == VAL_ILLEGAL_CF or not legal_pattern.match(val):
                    illegals.append(f"USER_CONFIG_GPIO_{windex}_INIT={val}")
                else:
                    valids[windex] = val
        
        # Check for missing and illegal
        if want:
            missing = [f"USER_CONFIG_GPIO_{i}_INIT" for i in sorted(want)]
            return self._make_failed_result(
                f"Missing {len(want)} GPIO definitions",
                details={'missing': missing}
            )
        
        if illegals:
            return self._make_failed_result(
                f"{len(illegals)} invalid GPIO values",
                details={'invalid': illegals}
            )
        
        # Write report
        try:
            report_path = context.report_dir / 'gpio_defines.report'
            with open(report_path, 'w') as f:
                for i in sorted(valids.keys()):
                    f.write(f"USER_CONFIG_GPIO_{i}_INIT  {valids[i]}\n")
        except Exception:
            pass  # Report writing is optional
        
        return self._make_passed_result(
            f"All {len(valids)} GPIO definitions valid",
            details={'gpio_count': len(valids)}
        )
