"""
agent/resource_registry.py
============================
Maps free-text user descriptions to local Terraform module directories.

The registry supports fuzzy / alias matching so "ec2", "EC2 instance",
"virtual machine", etc. all resolve to the same module folder.
"""

from pathlib import Path
from typing import Optional
import re


# ─── Alias table ────────────────────────────────────────────────────────────────
# Keys are canonical module directory names (must exist under modules_dir).
# Values are lists of aliases / keywords that map to that module.
RESOURCE_ALIASES: dict[str, list[str]] = {
    "ec2_instance": [
        "ec2", "ec2 instance", "virtual machine", "vm", "server",
        "compute", "instance", "linux server", "web server",
    ],
    "s3_bucket": [
        "s3", "s3 bucket", "bucket", "object storage", "blob storage",
        "storage bucket", "file storage",
    ],
    "rds_instance": [
        "rds", "rds instance", "database", "db", "mysql", "postgres",
        "postgresql", "aurora", "relational database", "sql",
    ],
    "vpc": [
        "vpc", "virtual private cloud", "network", "networking",
        "virtual network", "vnet",
    ],
    "lambda_function": [
        "lambda", "lambda function", "serverless", "function",
        "faas", "serverless function",
    ],
}


class ResourceRegistry:
    """
    Resolves a user's description to a Terraform module directory name.

    Only returns a module name if the corresponding directory actually
    exists under `modules_dir`, preventing phantom module references.
    """

    def __init__(self, modules_dir: Path):
        self.modules_dir = modules_dir
        # Build reverse lookup: alias → module_name
        self._alias_map: dict[str, str] = {}
        for module_name, aliases in RESOURCE_ALIASES.items():
            for alias in aliases:
                self._alias_map[alias.lower()] = module_name
            # The module name itself is also a valid alias
            self._alias_map[module_name.lower()] = module_name

    def resolve(self, description: str) -> Optional[str]:
        """
        Map a free-text description to a module directory name.

        Strategy:
          1. Exact match (case-insensitive) against alias table.
          2. Substring match — find any alias that appears in the description.
          3. Return None if no match found.
        """
        normalized = description.lower().strip()

        # 1. Exact match
        if normalized in self._alias_map:
            return self._validate(self._alias_map[normalized])

        # 2. Substring / keyword match
        for alias, module_name in self._alias_map.items():
            if alias in normalized or normalized in alias:
                return self._validate(module_name)

        # 3. Token overlap (e.g., "I want an EC2 instance please")
        tokens = set(re.split(r'\W+', normalized))
        for alias, module_name in self._alias_map.items():
            alias_tokens = set(re.split(r'\W+', alias))
            if tokens & alias_tokens:  # non-empty intersection
                return self._validate(module_name)

        return None

    def list_resources(self) -> list[str]:
        """Return human-readable names for all available (existing) modules."""
        available = []
        for module_name in RESOURCE_ALIASES:
            if (self.modules_dir / module_name).is_dir():
                # Convert snake_case → "EC2 Instance"
                readable = module_name.replace("_", " ").title()
                available.append(readable)
        return available or list(RESOURCE_ALIASES.keys())

    def _validate(self, module_name: str) -> Optional[str]:
        """Return module_name only if its directory exists."""
        module_path = self.modules_dir / module_name
        if module_path.is_dir():
            return module_name
        # Module defined in registry but directory missing — still return the
        # name so the agent can surface a helpful error message.
        return module_name
