"""
terraform/parser.py
=====================
Parses Terraform module files to extract variable metadata.

Primary entry-point: TerraformParser.get_required_variables(module_dir)

Returns a list of dicts:
  {
    "name":        "instance_type",
    "type":        "string",
    "description": "EC2 instance type (e.g. t3.micro)",
    "default":     null,          # null means required (no default)
    "validation":  {              # optional, from validation block
      "allowed_values": ["t3.micro", "t3.small"],
      "pattern": null
    }
  }

Parsing approach:
  - Primary:  python-hcl2 library for full AST parsing.
  - Fallback: regex-based extractor for environments where python-hcl2
              is not installed or fails on edge-case syntax.
"""

import re
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Sentinel to distinguish "no default set" from default=null/None
_NO_DEFAULT = object()


class TerraformParser:
    """
    Parses Terraform HCL files to discover module input variables.

    Usage:
        parser = TerraformParser()
        variables = parser.get_required_variables(Path("terraform_modules/ec2_instance"))
    """

    def get_required_variables(self, module_dir: Path) -> list[dict]:
        """
        Parse all .tf files in module_dir and return variable metadata.

        Returns only variables that lack a default value (i.e., are required),
        because those are the ones the user must supply.
        """
        if not module_dir.exists():
            raise FileNotFoundError(f"Module directory not found: {module_dir}")

        tf_files = list(module_dir.glob("*.tf"))
        if not tf_files:
            raise FileNotFoundError(f"No .tf files found in {module_dir}")

        all_variables: list[dict] = []
        for tf_file in tf_files:
            variables = self._parse_file(tf_file)
            all_variables.extend(variables)

        # Deduplicate by name (variables.tf may re-declare a var with defaults)
        seen: dict[str, dict] = {}
        for var in all_variables:
            name = var["name"]
            if name not in seen:
                seen[name] = var
            else:
                # Merge: prefer the entry that has more metadata
                if var.get("description") and not seen[name].get("description"):
                    seen[name]["description"] = var["description"]

        # Filter to required (no default) variables
        required = [v for v in seen.values() if v["default"] is None]
        optional = [v for v in seen.values() if v["default"] is not None]

        logger.info(
            "Module %s: %d required, %d optional variables",
            module_dir.name, len(required), len(optional),
        )
        return required

    def get_all_variables(self, module_dir: Path) -> list[dict]:
        """Return both required and optional variables (useful for display)."""
        if not module_dir.exists():
            raise FileNotFoundError(f"Module directory not found: {module_dir}")
        all_vars: list[dict] = []
        for tf_file in module_dir.glob("*.tf"):
            all_vars.extend(self._parse_file(tf_file))
        return all_vars

    # ─── Parsing logic ────────────────────────────────────────────────────────────

    def _parse_file(self, tf_file: Path) -> list[dict]:
        """Parse a single .tf file, trying HCL2 first then regex fallback."""
        content = tf_file.read_text(encoding="utf-8")
        try:
            return self._parse_hcl2(content, tf_file)
        except Exception as exc:
            logger.warning("HCL2 parse failed for %s (%s), using regex fallback", tf_file.name, exc)
            return self._parse_regex(content)

    def _parse_hcl2(self, content: str, source_file: Path) -> list[dict]:
        """Parse using the python-hcl2 library for accurate AST-based extraction."""
        import hcl2  # optional dependency

        parsed = hcl2.loads(content)
        variables = []
        for var_block in parsed.get("variable", []):
            for var_name, var_body in var_block.items():
                var_info = self._extract_var_info(var_name, var_body)
                variables.append(var_info)
        return variables

    def _parse_regex(self, content: str) -> list[dict]:
        """
        Regex-based fallback parser.
        Handles the common variable block pattern:
          variable "name" {
            type        = string
            description = "..."
            default     = "value"
          }
        """
        variables = []
        # Match each variable block
        block_pattern = re.compile(
            r'variable\s+"([^"]+)"\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
            re.DOTALL,
        )
        for match in block_pattern.finditer(content):
            var_name = match.group(1)
            block_body = match.group(2)
            var_info = self._extract_var_info_regex(var_name, block_body)
            variables.append(var_info)
        return variables

    # ─── Extraction helpers ───────────────────────────────────────────────────────

    def _extract_var_info(self, name: str, body: dict) -> dict:
        """Build a variable metadata dict from an HCL2-parsed variable body."""
        # Some hcl2 versions return quoted string literals (e.g. '"ami"').
        # Normalize them so downstream code always sees raw identifiers.
        clean_name = self._strip_wrapping_quotes(name)

        # Determine if a default is set
        has_default = "default" in body
        default_value = body.get("default")  # None if key absent

        # Normalise type to a simple string
        raw_type = body.get("type", "string")
        type_str = self._normalise_type(raw_type)

        # Extract validation constraints
        validation = self._extract_validation(body.get("validation", []))

        return {
            "name":        clean_name,
            "type":        type_str,
            "description": self._strip_wrapping_quotes(body.get("description", "")),
            "default":     default_value if has_default else None,
            "validation":  validation,
        }

    def _extract_var_info_regex(self, name: str, block_body: str) -> dict:
        """Build a variable metadata dict from raw block text."""
        def find_attr(pattern: str) -> Optional[str]:
            m = re.search(pattern, block_body)
            return m.group(1).strip() if m else None

        # Type
        raw_type = find_attr(r'type\s*=\s*(.+)') or "string"
        type_str = self._normalise_type(raw_type)

        # Description (strip quotes)
        description = find_attr(r'description\s*=\s*"([^"]*)"') or ""

        # Default (presence check)
        default_match = re.search(r'default\s*=\s*(.+)', block_body)
        if default_match:
            raw_default = default_match.group(1).strip().strip('"')
            default_value = raw_default
        else:
            default_value = None  # Required — no default

        # Simple validation: allowed_values from validation block
        validation: dict = {}
        allowed_match = re.search(
            r'condition\s*=\s*contains\(\[([^\]]+)\]', block_body
        )
        if allowed_match:
            raw_vals = allowed_match.group(1)
            allowed = [v.strip().strip('"') for v in raw_vals.split(",")]
            validation["allowed_values"] = allowed

        return {
            "name":        name,
            "type":        type_str,
            "description": description,
            "default":     default_value,
            "validation":  validation,
        }

    def _normalise_type(self, raw_type: Any) -> str:
        """Convert HCL type expressions to simple strings."""
        if isinstance(raw_type, str):
            # Strip ${} wrapper from old-style HCL
            t = raw_type.strip().strip("${}").lower()
            if t.startswith("list"):
                return "list"
            if t.startswith("map"):
                return "map"
            if t in ("bool", "boolean"):
                return "bool"
            if t in ("number", "int", "float"):
                return "number"
            return "string"
        # python-hcl2 returns type refs as objects
        return "string"

    def _extract_validation(self, validation_blocks: list) -> dict:
        """Extract structured validation constraints from HCL2 validation blocks."""
        result: dict = {}
        if not validation_blocks:
            return result
        for block in validation_blocks:
            condition = str(block.get("condition", ""))
            # Detect contains([...]) pattern for allowed values
            m = re.search(r'contains\(\[([^\]]+)\]', condition)
            if m:
                raw = m.group(1)
                result["allowed_values"] = [v.strip().strip('"') for v in raw.split(",")]
        return result

    def _strip_wrapping_quotes(self, value: Any) -> Any:
        """Trim one layer of matching wrapping quotes from string values."""
        if not isinstance(value, str):
            return value
        s = value.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
            return s[1:-1]
        return s
