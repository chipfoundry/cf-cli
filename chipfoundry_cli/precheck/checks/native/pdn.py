# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""PDN check - validates power distribution network configuration."""

import json
from pathlib import Path
from typing import Dict, Any, Optional

from chipfoundry_cli.precheck.checks.base import BaseCheck
from chipfoundry_cli.precheck.core.config import CheckContext
from chipfoundry_cli.precheck.core.results import CheckResult, CheckStatus
from chipfoundry_cli.precheck.core.registry import register_check


@register_check
class PdnCheck(BaseCheck):
    """Validates power distribution network (PDN) configuration.
    
    This check verifies that the OpenLane configuration has correct
    PDN pitch settings for the target PDK.
    """
    
    name = "pdn"
    display_name = "PDN Check"
    description = "Validates power distribution network configuration"
    category = "design"
    supported_pdks = ['gf180mcuC', 'gf180mcuD']  # Only for GF180 PDK
    supported_types = ['digital']
    requires_docker = False
    
    # Expected PDN configuration
    EXPECTED_HPITCH = "expr::60 + $FP_PDN_HPITCH_MULT * 30"
    
    def run(self, context: CheckContext) -> CheckResult:
        """Execute the PDN check.
        
        Args:
            context: Check execution context
            
        Returns:
            CheckResult indicating the check outcome
        """
        # Get the OpenLane config path
        config_path = context.project_path / 'openlane' / context.user_module / 'config.json'
        
        # Check if config exists
        if not config_path.exists():
            # Try alternate location
            config_path = context.project_path / 'openlane' / 'user_proj_example' / 'config.json'
            
        if not config_path.exists():
            return self._make_failed_result(
                f"OpenLane configuration not found",
                details={'searched_paths': [
                    str(context.project_path / 'openlane' / context.user_module / 'config.json'),
                    str(context.project_path / 'openlane' / 'user_proj_example' / 'config.json'),
                ]}
            )
        
        # Read and parse config
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            return self._make_error_result(f"Invalid JSON in config: {e}")
        except Exception as e:
            return self._make_error_result(f"Failed to read config: {e}")
        
        # Check required fields
        errors = []
        
        if "FP_PDN_HPITCH" not in config:
            errors.append("FP_PDN_HPITCH not defined")
        
        if "FP_PDN_HPITCH_MULT" not in config:
            errors.append("FP_PDN_HPITCH_MULT not defined")
        
        if errors:
            return self._make_failed_result(
                f"Missing PDN configuration: {', '.join(errors)}",
                details={'missing_fields': errors}
            )
        
        # Validate FP_PDN_HPITCH_MULT
        hpitch_mult = config["FP_PDN_HPITCH_MULT"]
        
        if isinstance(hpitch_mult, str):
            return self._make_failed_result(
                "FP_PDN_HPITCH_MULT cannot be a string",
                details={'value': hpitch_mult, 'expected': 'integer >= 0'}
            )
        
        if not isinstance(hpitch_mult, int):
            return self._make_failed_result(
                "FP_PDN_HPITCH_MULT must be an integer",
                details={'value': hpitch_mult, 'type': type(hpitch_mult).__name__}
            )
        
        if hpitch_mult < 0:
            return self._make_failed_result(
                "FP_PDN_HPITCH_MULT cannot be negative",
                details={'value': hpitch_mult}
            )
        
        # Validate FP_PDN_HPITCH
        hpitch = config["FP_PDN_HPITCH"]
        
        if hpitch != self.EXPECTED_HPITCH:
            return self._make_failed_result(
                "FP_PDN_HPITCH has incorrect value",
                details={
                    'actual': hpitch,
                    'expected': self.EXPECTED_HPITCH
                }
            )
        
        return self._make_passed_result(
            "PDN configuration valid",
            details={
                'FP_PDN_HPITCH': hpitch,
                'FP_PDN_HPITCH_MULT': hpitch_mult
            }
        )
