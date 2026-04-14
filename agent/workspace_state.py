"""
agent/workspace_state.py
==========================
Tracks multiple Terraform module instances per type in a JSON sidecar so the
chatbot can add/update/delete logical resources without replacing unrelated
instances.

EC2 (and other types) use a stable for_each key derived from the identity
variable (e.g. Name tag for EC2). Same identity → update values in place;
different identity → new instance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

STATE_FILENAME = "chatbot_workspace_state.json"

# Module subdirectory name → variable used as user-visible identity / for_each key basis
IDENTITY_VAR_BY_MODULE: dict[str, str] = {
    "ec2_instance": "name",
    "s3_bucket": "bucket_name",
    "rds_instance": "db_identifier",
}

def sanitize_for_each_key(identity: str) -> str:
    """
    Produce a valid Terraform for_each map key (string key in HCL object).
    Uses lowercase and underscores; avoids leading digits.
    """
    s = identity.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "instance"
    if s[0].isdigit():
        s = "n_" + s
    return s[:120]


def module_type_from_source_path(source: str) -> Optional[str]:
    """Infer module type folder name from module source path."""
    s = source.strip().strip('"')
    m = re.search(r"terraform_modules[/\\\\]([a-z0-9_]+)", s, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"[/\\\\]([a-z0-9_]+)$", s, re.I)
    return m.group(1).lower() if m else None


def migrate_legacy_main_tf(main_tf: Path) -> dict[str, Any]:
    """
    If main.tf contains a single `module "resource" { ... }`, parse attributes
    into workspace state. Returns empty by_type if parsing fails.
    """
    if not main_tf.exists():
        return _empty_state()

    text = main_tf.read_text(encoding="utf-8")
    if 'module "resource"' not in text and "module 'resource'" not in text:
        return _empty_state()

    # Grab first module "resource" block (rough parse)
    m = re.search(
        r'module\s+"resource"\s*\{([\s\S]*?)\n\}',
        text,
    )
    if not m:
        return _empty_state()

    body = m.group(1)
    src_m = re.search(r'source\s*=\s*"([^"]+)"', body)
    if not src_m:
        return _empty_state()

    mod_type = module_type_from_source_path(src_m.group(1))
    if not mod_type or mod_type not in IDENTITY_VAR_BY_MODULE:
        return _empty_state()

    identity_var = IDENTITY_VAR_BY_MODULE[mod_type]
    values: dict[str, Any] = {}
    for attr_m in re.finditer(
        r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)\s*$",
        body,
        re.MULTILINE,
    ):
        name, raw = attr_m.group(1), attr_m.group(2).strip()
        if name == "source":
            continue
        values[name] = _parse_hcl_literal(raw)

    if identity_var not in values:
        return _empty_state()

    key = _allocate_key(mod_type, {}, str(values[identity_var]))
    return {
        "version": 1,
        "by_type": {mod_type: {key: values}},
    }


def _parse_hcl_literal(raw: str) -> Any:
    raw = raw.strip()
    if re.fullmatch(r"\d+", raw):
        return int(raw)
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith('"') and raw.endswith('"'):
        inner = raw[1:-1]
        return inner.replace('\\"', '"')
    return raw


def _empty_state() -> dict[str, Any]:
    return {"version": 1, "by_type": {}}


def _allocate_key(module_type: str, existing: dict[str, dict], identity_raw: str) -> str:
    """
    Return the for_each key for this identity. Reuses the key whose stored
    identity variable matches identity_raw; otherwise allocates a unique key.
    """
    ivar = IDENTITY_VAR_BY_MODULE.get(module_type)
    if ivar:
        for k, v in existing.items():
            if isinstance(v, dict) and str(v.get(ivar, "")) == str(identity_raw):
                return k

    base = sanitize_for_each_key(str(identity_raw))
    key = base
    n = 2
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


class WorkspaceState:
    """Load/save chatbot_workspace_state.json and merge Terraform instances."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = Path(workspace_dir)
        self.path = self.workspace_dir / STATE_FILENAME

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if "by_type" not in data:
                    data["by_type"] = {}
                data.setdefault("version", 1)
                return data
            except (json.JSONDecodeError, OSError):
                pass

        migrated = migrate_legacy_main_tf(self.workspace_dir / "main.tf")
        if migrated["by_type"]:
            self.save(migrated)
            return migrated
        return _empty_state()

    def save(self, state: dict[str, Any]) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def upsert(
        self,
        module_type: str,
        collected_values: dict[str, Any],
        module_var_names: list[str],
    ) -> tuple[dict[str, Any], str]:
        """
        Merge collected values into state for module_type.

        Returns (state, for_each_key_used).
        """
        state = self.load()
        by_type: dict[str, dict] = state.setdefault("by_type", {})

        identity_var = IDENTITY_VAR_BY_MODULE.get(module_type)
        if not identity_var or identity_var not in collected_values:
            # Fallback: single-instance replace for unknown types
            key = "default"
            filtered = _filter_vars(collected_values, module_var_names)
            by_type[module_type] = {key: filtered}
            self.save(state)
            return state, key

        filtered = _filter_vars(collected_values, module_var_names)
        identity_val = filtered.get(identity_var)
        if identity_val is None:
            raise ValueError(f"Missing identity variable {identity_var!r} for {module_type}")

        instances = by_type.setdefault(module_type, {})
        key = _allocate_key(module_type, instances, str(identity_val))
        instances[key] = filtered
        self.save(state)
        return state, key

    def delete_by_identity(
        self,
        module_type: str,
        identity_value: str,
    ) -> tuple[bool, str]:
        """
        Remove the instance whose identity variable (e.g. Name tag) equals
        identity_value. Returns (removed, message).
        """
        state = self.load()
        by_type = state.get("by_type", {})
        instances = by_type.get(module_type)
        if not instances:
            return False, f"No {module_type} instances in workspace state."

        ivar = IDENTITY_VAR_BY_MODULE.get(module_type, "name")
        to_del: Optional[str] = None
        for k, v in instances.items():
            if isinstance(v, dict) and str(v.get(ivar, "")) == identity_value:
                to_del = k
                break

        if to_del is None:
            return False, (
                f"No {module_type} with {ivar}={identity_value!r}. "
                f"Use list_managed_resources to see names."
            )

        del instances[to_del]
        if not instances:
            del by_type[module_type]
        self.save(state)
        return True, f"Removed {module_type!r} key {to_del!r} from workspace state."

    def delete_any_type_by_identity(self, identity_value: str) -> tuple[bool, str]:
        """Try deleting from any module type that has a matching identity field."""
        state = self.load()
        by_type = state.get("by_type", {})
        for mod_type in list(by_type.keys()):
            ivar = IDENTITY_VAR_BY_MODULE.get(mod_type)
            if not ivar:
                continue
            instances = by_type[mod_type]
            for k, v in list(instances.items()):
                if isinstance(v, dict) and str(v.get(ivar, "")) == identity_value:
                    del instances[k]
                    if not instances:
                        del by_type[mod_type]
                    self.save(state)
                    return True, f"Removed {mod_type} ({ivar}={identity_value!r})."
        return False, f"No resource found with identity {identity_value!r}."

    def list_summary(self) -> list[dict[str, Any]]:
        """Human/agent-friendly rows for each managed instance."""
        state = self.load()
        rows: list[dict[str, Any]] = []
        for mod_type, instances in state.get("by_type", {}).items():
            ivar = IDENTITY_VAR_BY_MODULE.get(mod_type, "name")
            for tf_key, vals in instances.items():
                if not isinstance(vals, dict):
                    continue
                rows.append({
                    "module_type": mod_type,
                    "terraform_key": tf_key,
                    "identity_var": ivar,
                    "identity_value": vals.get(ivar),
                    "instance_type": vals.get("instance_type"),
                    "ami": vals.get("ami"),
                })
        return rows


def _filter_vars(collected: dict[str, Any], module_var_names: list[str]) -> dict[str, Any]:
    from terraform.generator import PROVIDER_LEVEL_VARS

    out: dict[str, Any] = {}
    for k, v in collected.items():
        ck = k.strip().strip('"').strip("'").strip()
        if ck.lower() in PROVIDER_LEVEL_VARS:
            continue
        if module_var_names and ck not in module_var_names:
            continue
        out[ck] = v
    return out
