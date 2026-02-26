"""Configuration loader for meetings2obsidian."""

import logging
import os
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Handles loading and validation of configuration files."""

    DEFAULT_CONFIG_PATHS: ClassVar[List[str]] = [
        "config.yaml",
        os.path.expanduser("~/.config/meetings2obsidian/config.yaml"),
    ]

    def __init__(self, config_path: Optional[str] = None):
        """Initialize the configuration loader.

        Args:
            config_path: Optional path to config file. If not provided, searches default locations.
        """
        self.config_path = self._find_config(config_path)
        self.config = self._load_config()
        self._validate_config()

    def _find_config(self, config_path: Optional[str] = None) -> Path:
        """Find the configuration file.

        Args:
            config_path: Optional explicit path to config file.

        Returns:
            Path to the configuration file.

        Raises:
            FileNotFoundError: If no configuration file is found.
        """
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"Config file not found: {config_path}")
            return path

        for default_path in self.DEFAULT_CONFIG_PATHS:
            path = Path(default_path)
            if path.exists():
                logger.info(f"Using config file: {path}")
                return path

        raise FileNotFoundError(
            f"No config file found. Searched: {self.DEFAULT_CONFIG_PATHS}. "
            "Please create a config.yaml file or specify --config."
        )

    def _load_config(self) -> Dict[str, Any]:
        """Load the YAML configuration file.

        Returns:
            Dictionary containing configuration values.

        Raises:
            ValueError: If the config file is malformed.
        """
        try:
            with open(self.config_path) as f:
                config = yaml.safe_load(f)

            if config is None:
                raise ValueError("Config file is empty")

            return config
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in config file: {e}") from e

    def _validate_config(self) -> None:
        """Validate required configuration fields.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        required_fields = ["obsidian_vault_path", "output_folder"]

        for field in required_fields:
            if field not in self.config:
                raise ValueError(f"Missing required config field: {field}")

        # Validate vault path exists
        vault_path = Path(self.config["obsidian_vault_path"])
        if not vault_path.exists():
            raise ValueError(f"Obsidian vault path does not exist: {vault_path}")

        if not vault_path.is_dir():
            raise ValueError(f"Obsidian vault path is not a directory: {vault_path}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value.

        Args:
            key: Configuration key (supports dot notation for nested values).
            default: Default value if key is not found.

        Returns:
            Configuration value or default.
        """
        keys = key.split(".")
        value = self.config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value

    def get_vault_path(self) -> Path:
        """Get the Obsidian vault path.

        Returns:
            Path to the Obsidian vault.
        """
        return Path(self.config["obsidian_vault_path"])

    def get_output_path(self) -> Path:
        """Get the full output path within the vault.

        Returns:
            Path to the output folder.
        """
        return self.get_vault_path() / self.config["output_folder"]

    def is_platform_enabled(self, platform: str) -> bool:
        """Check if a platform is enabled.

        Args:
            platform: Platform name (heypocket, googlemeet, zoom).

        Returns:
            True if platform is enabled, False otherwise.
        """
        platforms = self.config.get("platforms", {})
        platform_config = platforms.get(platform, {})
        return platform_config.get("enabled", True)

    def get_platform_config(self, platform: str) -> Dict[str, Any]:
        """Get configuration for a specific platform.

        Args:
            platform: Platform name (heypocket, googlemeet, zoom).

        Returns:
            Platform configuration dictionary.
        """
        platforms = self.config.get("platforms", {})
        return platforms.get(platform, {})
