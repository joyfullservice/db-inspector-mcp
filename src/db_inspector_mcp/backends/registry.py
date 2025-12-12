"""Backend registry for managing multiple database connections."""

from typing import Any

from .base import DatabaseBackend


class BackendRegistry:
    """Registry for managing multiple database backend instances."""
    
    def __init__(self):
        """Initialize an empty registry."""
        self._backends: dict[str, DatabaseBackend] = {}
        self._default_name: str | None = None
    
    def register(self, name: str, backend: DatabaseBackend, set_as_default: bool = False) -> None:
        """
        Register a backend with a name.
        
        Args:
            name: Unique name for this backend
            backend: DatabaseBackend instance
            set_as_default: If True, set this as the default backend
        """
        if not name:
            raise ValueError("Backend name cannot be empty")
        if not isinstance(backend, DatabaseBackend):
            raise TypeError("backend must be an instance of DatabaseBackend")
        
        self._backends[name] = backend
        if set_as_default or self._default_name is None:
            self._default_name = name
    
    def get(self, name: str | None = None) -> DatabaseBackend:
        """
        Get a backend by name, or the default if name is None.
        
        Args:
            name: Name of the backend, or None for default
            
        Returns:
            DatabaseBackend instance
            
        Raises:
            ValueError: If backend name is not found or no default is set
        """
        if name is None:
            if self._default_name is None:
                raise ValueError("No default backend set and no name provided")
            name = self._default_name
        
        if name not in self._backends:
            available = ", ".join(self._backends.keys())
            raise ValueError(
                f"Backend '{name}' not found. Available backends: {available}"
            )
        
        return self._backends[name]
    
    def list_backends(self) -> list[str]:
        """
        List all registered backend names.
        
        Returns:
            List of backend names
        """
        return list(self._backends.keys())
    
    def get_default_name(self) -> str | None:
        """
        Get the name of the default backend.
        
        Returns:
            Default backend name, or None if not set
        """
        return self._default_name
    
    def set_default(self, name: str) -> None:
        """
        Set the default backend by name.
        
        Args:
            name: Name of the backend to set as default
            
        Raises:
            ValueError: If backend name is not found
        """
        if name not in self._backends:
            available = ", ".join(self._backends.keys())
            raise ValueError(
                f"Backend '{name}' not found. Available backends: {available}"
            )
        self._default_name = name


# Global registry instance
_registry = BackendRegistry()


def get_registry() -> BackendRegistry:
    """Get the global backend registry."""
    return _registry
