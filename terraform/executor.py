"""
terraform/executor.py
=======================
Executes Terraform CLI commands via subprocess with:
  - Streaming stdout/stderr output
  - Configurable timeouts
  - Retry logic for transient failures
  - Structured result dicts

Public methods:
  run(workspace_dir)          → init + plan + apply
  get_outputs(workspace_dir)  → terraform output -json
  destroy(workspace_dir)      → terraform destroy (with confirmation)
"""

import subprocess
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

# ─── Configuration constants ─────────────────────────────────────────────────────
TERRAFORM_BIN = "terraform"          # or full path e.g. "/usr/local/bin/terraform"
INIT_TIMEOUT_S = 120                 # terraform init can be slow (plugin downloads)
PLAN_TIMEOUT_S = 60
APPLY_TIMEOUT_S = 300
OUTPUT_TIMEOUT_S = 30
MAX_RETRIES = 2                      # retry transient failures (e.g. provider API glitches)
RETRY_DELAY_S = 5


class TerraformExecutor:
    """
    Wraps Terraform CLI commands in a Python-friendly interface.

    Args:
        logger: Optional Python logger. Creates a module-level one if omitted.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._verify_terraform_binary()

    # ─── Public API ──────────────────────────────────────────────────────────────

    def run(
        self,
        workspace_dir: Path,
        after_init: Optional[Callable[[Path], None]] = None,
    ) -> dict:
        """
        Execute the full Terraform lifecycle: init → plan → apply.

        Args:
            workspace_dir: Terraform working directory.
            after_init: Optional hook invoked after a successful init (e.g. state mv).

        Returns:
            {
              "success": bool,
              "stdout":  str,
              "stderr":  str,
              "outputs": dict   # populated on success
            }
        """
        results = {"success": False, "stdout": "", "stderr": "", "outputs": {}}

        # ── 1. init ──────────────────────────────────────────────────────────────
        print("\n  ⏳  Running terraform init...")
        init_result = self._run_with_retry(
            ["terraform", "init", "-no-color"],
            workspace_dir,
            timeout=INIT_TIMEOUT_S,
        )
        if not init_result["success"]:
            results["stderr"] = f"[init failed]\n{init_result['stderr']}"
            return results
        print("  ✅  terraform init complete.")
        results["stdout"] += init_result["stdout"]

        if after_init:
            after_init(workspace_dir)

        # ── 2. plan ───────────────────────────────────────────────────────────────
        print("  ⏳  Running terraform plan...")
        plan_result = self._run_with_retry(
            ["terraform", "plan", "-no-color", "-out=tfplan"],
            workspace_dir,
            timeout=PLAN_TIMEOUT_S,
        )
        if not plan_result["success"]:
            results["stderr"] = f"[plan failed]\n{plan_result['stderr']}"
            return results
        print("  ✅  terraform plan complete.")
        results["stdout"] += "\n" + plan_result["stdout"]

        # ── 3. approval gate (after plan, before apply) ─────────────────────────
        print()
        confirm = input("❓  Plan generated. Proceed with terraform apply? [yes/no]: ").strip().lower()
        if confirm not in ("yes", "y"):
            results["success"] = False
            results["stderr"] = "Terraform apply cancelled by user."
            return results

        # ── 4. apply ──────────────────────────────────────────────────────────────
        print("  ⏳  Running terraform apply...")
        apply_result = self._run_with_retry(
            ["terraform", "apply", "-no-color", "tfplan"],
            workspace_dir,
            timeout=APPLY_TIMEOUT_S,
        )
        results["stdout"] += "\n" + apply_result["stdout"]
        results["stderr"] = apply_result["stderr"]
        results["success"] = apply_result["success"]

        if results["success"]:
            print("  ✅  terraform apply complete.")
            # Collect outputs
            output_result = self.get_outputs(workspace_dir)
            if output_result["success"]:
                results["outputs"] = output_result.get("parsed", {})

        return results

    def get_outputs(self, workspace_dir: Path) -> dict:
        """
        Run `terraform output -json` and return parsed outputs.

        Returns:
            {
              "success": bool,
              "stdout":  str (raw JSON),
              "stderr":  str,
              "parsed":  dict
            }
        """
        result = self._execute(
            ["terraform", "output", "-no-color", "-json"],
            workspace_dir,
            timeout=OUTPUT_TIMEOUT_S,
        )
        if result["success"]:
            import json
            try:
                result["parsed"] = json.loads(result["stdout"])
            except json.JSONDecodeError:
                result["parsed"] = {}
        return result

    def state_list(self, workspace_dir: Path) -> dict:
        """Run `terraform state list`. Requires initialized workspace."""
        return self._execute(
            ["terraform", "state", "list", "-no-color"],
            workspace_dir,
            timeout=OUTPUT_TIMEOUT_S,
        )

    def state_mv(self, workspace_dir: Path, from_addr: str, to_addr: str) -> dict:
        """Run `terraform state mv` between two addresses."""
        return self._execute(
            ["terraform", "state", "mv", "-no-color", from_addr, to_addr],
            workspace_dir,
            timeout=60,
        )

    def destroy(self, workspace_dir: Path) -> dict:
        """
        Run `terraform destroy -auto-approve`.
        Should only be called after an explicit user confirmation.
        """
        return self._run_with_retry(
            ["terraform", "destroy", "-no-color", "-auto-approve"],
            workspace_dir,
            timeout=APPLY_TIMEOUT_S,
        )

    # ─── Internal helpers ─────────────────────────────────────────────────────────

    def _run_with_retry(self, cmd: list, cwd: Path, timeout: int) -> dict:
        """Execute a command, retrying on transient failure."""
        last_result: dict = {}
        for attempt in range(1, MAX_RETRIES + 1):
            last_result = self._execute(cmd, cwd, timeout)
            if last_result["success"]:
                return last_result
            is_last = (attempt == MAX_RETRIES)
            if not is_last:
                self.logger.warning(
                    "Command failed (attempt %d/%d), retrying in %ds: %s",
                    attempt, MAX_RETRIES, RETRY_DELAY_S, " ".join(cmd),
                )
                print(f"  ⚠️   Attempt {attempt} failed. Retrying in {RETRY_DELAY_S}s...")
                time.sleep(RETRY_DELAY_S)
        return last_result

    def _execute(self, cmd: list, cwd: Path, timeout: int) -> dict:
        """
        Run a subprocess command and return a structured result dict.

        Streams output lines to the console while also capturing them.
        """
        self.logger.info("Executing: %s  (cwd=%s)", " ".join(cmd), cwd)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            # Stream stdout in real time
            for line in process.stdout:
                stripped = line.rstrip()
                stdout_lines.append(stripped)
                print(f"     {stripped}")

            # Collect stderr after process finishes
            _, stderr_raw = process.communicate(timeout=timeout)
            stderr_lines = stderr_raw.splitlines()

            return_code = process.returncode
            success = (return_code == 0)

            if not success:
                self.logger.error(
                    "Command failed (rc=%d): %s\nSTDERR:\n%s",
                    return_code, " ".join(cmd), "\n".join(stderr_lines),
                )

            return {
                "success": success,
                "stdout":  "\n".join(stdout_lines),
                "stderr":  "\n".join(stderr_lines),
                "returncode": return_code,
            }

        except subprocess.TimeoutExpired:
            process.kill()
            self.logger.error("Command timed out after %ds: %s", timeout, " ".join(cmd))
            return {
                "success":    False,
                "stdout":     "\n".join(stdout_lines),
                "stderr":     f"Command timed out after {timeout} seconds.",
                "returncode": -1,
            }
        except FileNotFoundError:
            msg = f"Terraform binary not found. Is '{TERRAFORM_BIN}' installed and on PATH?"
            self.logger.error(msg)
            return {"success": False, "stdout": "", "stderr": msg, "returncode": -1}
        except Exception as exc:
            self.logger.exception("Unexpected error running %s: %s", cmd, exc)
            return {"success": False, "stdout": "", "stderr": str(exc), "returncode": -1}

    def _verify_terraform_binary(self):
        """Warn (but don't crash) if the terraform binary is not on PATH."""
        if not shutil.which(TERRAFORM_BIN):
            self.logger.warning(
                "Terraform binary '%s' not found on PATH. "
                "Commands will fail at runtime unless Terraform is installed.",
                TERRAFORM_BIN,
            )
