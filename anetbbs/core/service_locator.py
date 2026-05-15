# anetbbs/core/service_locator.py
from typing import Dict, Any, TypeVar

T = TypeVar('T')

class ServiceLocator:
    _instances: Dict[str, Any] = {}
    
    @classmethod
    def register(cls, key: str, instance: Any) -> None:
        """Register a service instance with a key"""
        cls._instances[key] = instance
    
    @classmethod
    def get(cls, key: str) -> Any:
        """Get a service instance by key"""
        return cls._instances.get(key)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered services"""
        cls._instances.clear()
