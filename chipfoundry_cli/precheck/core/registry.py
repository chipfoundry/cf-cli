# SPDX-FileCopyrightText: 2024 ChipFoundry
# SPDX-License-Identifier: Apache-2.0
"""Check registration and discovery system."""

import importlib
import pkgutil
from typing import Dict, List, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from chipfoundry_cli.precheck.checks.base import BaseCheck


class CheckRegistry:
    """Discovers and manages available precheck checks.
    
    The registry maintains a collection of check classes that can be
    filtered by various criteria (category, PDK support, etc.).
    """
    
    def __init__(self):
        """Initialize an empty registry."""
        self._checks: Dict[str, Type['BaseCheck']] = {}
        self._discovered = False
    
    def register(self, check_class: Type['BaseCheck']):
        """Register a check class.
        
        Args:
            check_class: The check class to register
        """
        self._checks[check_class.name] = check_class
    
    def get(self, name: str) -> Optional[Type['BaseCheck']]:
        """Get a check class by name.
        
        Args:
            name: The check name
            
        Returns:
            The check class, or None if not found
        """
        return self._checks.get(name)
    
    def get_all(self) -> Dict[str, Type['BaseCheck']]:
        """Get all registered checks.
        
        Returns:
            Dictionary mapping check names to check classes
        """
        return self._checks.copy()
    
    def get_checks(self,
                   categories: Optional[List[str]] = None,
                   pdk: Optional[str] = None,
                   project_type: Optional[str] = None,
                   native_only: bool = False) -> List[Type['BaseCheck']]:
        """Get a filtered list of check classes.
        
        Args:
            categories: Only include checks in these categories
            pdk: Only include checks that support this PDK
            project_type: Only include checks that support this project type
            native_only: Only include checks that don't require Docker
            
        Returns:
            List of matching check classes
        """
        result = []
        
        for check_class in self._checks.values():
            # Filter by native_only
            if native_only and check_class.requires_docker:
                continue
            
            # Filter by category
            if categories and check_class.category not in categories:
                continue
            
            # Filter by PDK support
            if pdk and pdk not in check_class.supported_pdks:
                continue
            
            # Filter by project type support
            if project_type and project_type not in check_class.supported_types:
                continue
            
            result.append(check_class)
        
        return result
    
    def get_categories(self) -> List[str]:
        """Get all unique check categories.
        
        Returns:
            Sorted list of category names
        """
        categories = set()
        for check_class in self._checks.values():
            categories.add(check_class.category)
        return sorted(categories)
    
    def list_checks(self) -> List[Dict[str, str]]:
        """Get a list of all checks with their metadata.
        
        Returns:
            List of dictionaries with check info
        """
        result = []
        for check_class in self._checks.values():
            result.append({
                'name': check_class.name,
                'display_name': check_class.display_name,
                'category': check_class.category,
                'requires_docker': check_class.requires_docker,
                'description': check_class.description,
            })
        return sorted(result, key=lambda x: (x['category'], x['name']))
    
    def auto_discover(self):
        """Auto-discover and register checks from native/ and docker/ packages.
        
        This imports all check modules and registers any check classes
        decorated with @register_check or that have a 'register' attribute.
        """
        if self._discovered:
            return
        
        # Import the checks packages to trigger registration
        try:
            from chipfoundry_cli.precheck.checks import native
            from chipfoundry_cli.precheck.checks import docker
            
            # Import all modules in native package
            for importer, modname, ispkg in pkgutil.iter_modules(native.__path__):
                if not modname.startswith('_'):
                    importlib.import_module(f'chipfoundry_cli.precheck.checks.native.{modname}')
            
            # Import all modules in docker package
            for importer, modname, ispkg in pkgutil.iter_modules(docker.__path__):
                if not modname.startswith('_'):
                    importlib.import_module(f'chipfoundry_cli.precheck.checks.docker.{modname}')
                    
        except ImportError as e:
            # Silently ignore if packages don't exist yet
            pass
        
        self._discovered = True


# Global registry instance
_global_registry: Optional[CheckRegistry] = None


def get_registry() -> CheckRegistry:
    """Get the global check registry instance.
    
    Returns:
        The global CheckRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = CheckRegistry()
    return _global_registry


def register_check(cls: Type['BaseCheck']) -> Type['BaseCheck']:
    """Decorator to register a check class with the global registry.
    
    Usage:
        @register_check
        class MyCheck(BaseCheck):
            name = "my_check"
            ...
    
    Args:
        cls: The check class to register
        
    Returns:
        The check class (unchanged)
    """
    get_registry().register(cls)
    return cls
