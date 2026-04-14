"""
agent/input_collector.py
==========================
Collects and validates individual Terraform variable values from the user.

Each call to `collect()` prompts the user, applies type coercion, and
runs validation rules (allowed values, regex patterns, min/max bounds).
Retries up to MAX_RETRIES times before raising a ValueError.
"""

import re
from typing import Any

MAX_RETRIES = 3


class InputCollector:
    """
    Interactive, validating prompt for a single Terraform variable.

    Supported var_types:
        string  - raw text (default)
        number  - int or float
        bool    - true/false/yes/no
        list    - comma-separated values parsed into a Python list
        map     - key=value pairs parsed into a Python dict

    Validation dict keys (all optional):
        allowed_values  : list of permitted raw strings
        pattern         : regex the raw string must match
        min             : numeric minimum (for number type)
        max             : numeric maximum (for number type)
        min_length      : minimum string length
        max_length      : maximum string length
    """

    def collect(
        self,
        variable_name: str,
        prompt: str,
        var_type: str = "string",
        validation: dict = None,
    ) -> Any:
        """
        Prompt the user for a variable value and return the validated, typed result.

        Args:
            variable_name: Terraform variable name (used in error messages).
            prompt:        Human-readable question to display.
            var_type:      Expected Terraform type.
            validation:    Optional dict of validation constraints.

        Returns:
            The coerced, validated value.

        Raises:
            ValueError: If the user fails validation after MAX_RETRIES attempts.
        """
        validation = validation or {}

        for attempt in range(1, MAX_RETRIES + 1):
            raw = input(f"  ➜  {prompt}: ").strip()

            if not raw:
                print(f"  ⚠️   Value cannot be empty. Please try again.")
                continue

            # Type coercion
            try:
                value = self._coerce(raw, var_type)
            except (ValueError, TypeError) as exc:
                print(f"  ⚠️   Invalid {var_type}: {exc}. Please try again.")
                continue

            # Validation rules
            error = self._validate(value, raw, validation, var_type)
            if error:
                print(f"  ⚠️   {error}")
                if attempt < MAX_RETRIES:
                    print(f"  ℹ️   {MAX_RETRIES - attempt} attempt(s) remaining.")
                continue

            return value  # ✅ Success

        raise ValueError(
            f"Failed to collect valid value for '{variable_name}' after {MAX_RETRIES} attempts."
        )

    # ─── Private helpers ─────────────────────────────────────────────────────────

    def _coerce(self, raw: str, var_type: str) -> Any:
        """Convert a raw string to the target Python type."""
        vt = var_type.lower()

        if vt == "string":
            return raw

        if vt == "number":
            # Try int first, then float
            try:
                return int(raw)
            except ValueError:
                return float(raw)

        if vt in ("bool", "boolean"):
            if raw.lower() in ("true", "yes", "1", "on"):
                return True
            if raw.lower() in ("false", "no", "0", "off"):
                return False
            raise ValueError(f"Expected true/false, got '{raw}'")

        if vt == "list":
            # Comma-separated → list of stripped strings
            return [item.strip() for item in raw.split(",") if item.strip()]

        if vt == "map":
            # key=value pairs (comma or newline separated)
            result = {}
            for pair in re.split(r'[,\n]', raw):
                pair = pair.strip()
                if '=' in pair:
                    k, _, v = pair.partition('=')
                    result[k.strip()] = v.strip()
            return result

        # Unknown type — return as string
        return raw

    def _validate(self, value: Any, raw: str, validation: dict, var_type: str) -> str:
        """Return an error message string, or empty string if valid."""

        # Allowed values check (compare against raw string for UX clarity)
        if "allowed_values" in validation:
            allowed = [str(a).lower() for a in validation["allowed_values"]]
            if raw.lower() not in allowed:
                return (
                    f"Value must be one of: {', '.join(validation['allowed_values'])}. "
                    f"Got: '{raw}'"
                )

        # Regex pattern
        if "pattern" in validation:
            if not re.fullmatch(validation["pattern"], raw):
                return f"Value '{raw}' does not match required pattern: {validation['pattern']}"

        # Numeric bounds
        if var_type == "number":
            if "min" in validation and value < validation["min"]:
                return f"Value must be ≥ {validation['min']}. Got: {value}"
            if "max" in validation and value > validation["max"]:
                return f"Value must be ≤ {validation['max']}. Got: {value}"

        # String length
        if var_type == "string":
            s = str(value)
            if "min_length" in validation and len(s) < validation["min_length"]:
                return f"Value must be at least {validation['min_length']} characters."
            if "max_length" in validation and len(s) > validation["max_length"]:
                return f"Value must be at most {validation['max_length']} characters."

        return ""  # ✅ No validation errors
