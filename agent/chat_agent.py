"""
agent/chat_agent.py
====================
Core Agentic AI orchestrator using Groq as the LLM backend.

Key design: variable names are NEVER hardcoded in the system prompt.
Instead, after identify_resource + get_module_variables, the agent
injects a dynamic "module context" block into the conversation so the
LLM knows exactly which variable names, types, and validations the
current module declares. This makes the agent work correctly for any
Terraform module without code changes.

Workflow per resource:
  identify_resource → get_module_variables → collect_variable_value (×N)
  → generate_config → run_terraform → show_outputs → plain-text reply
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from groq import Groq

from terraform.parser import TerraformParser
from terraform.generator import TerraformGenerator
from terraform.executor import TerraformExecutor
from agent.input_collector import InputCollector
from agent.resource_registry import ResourceRegistry
from agent.workspace_state import WorkspaceState
from utils.logger import audit_log


GROQ_MODEL      = "llama-3.1-8b-instant"
MAX_TOKENS      = 1024
MAX_TOOL_ROUNDS = 40   # enough for a module with ~15 variables

# Never ask the user for these — they come from environment variables
SKIP_VARS = {"aws_region", "region"}

# ─── Base system prompt (NO variable names hardcoded) ───────────────────────
BASE_SYSTEM_PROMPT = """You are a precise AWS infrastructure assistant that
provisions cloud resources using Terraform.

## Tools
Respond with ONLY a raw JSON object when calling a tool — no markdown, no prose.

{"tool": "identify_resource",      "args": {"description": "<user text>"}}
{"tool": "get_module_variables",   "args": {}}
{"tool": "collect_variable_value", "args": {"variable_name": "...", "prompt": "...", "var_type": "string|number|bool|list", "validation": {}}}
{"tool": "generate_config",        "args": {}}
{"tool": "run_terraform",          "args": {}}
{"tool": "show_outputs",           "args": {}}
{"tool": "list_managed_resources", "args": {}}
{"tool": "delete_managed_resource", "args": {"identity_name": "<EC2 Name tag or bucket/db id>", "module_type": "<optional e.g. ec2_instance>"}}

## Mandatory workflow — follow every step, every time

1. User mentions a resource → call identify_resource immediately.
2. Call get_module_variables → you will receive the list of required variables
   as JSON. This list is your source of truth — use ONLY the variable names,
   types, and validation rules from this list. Never invent alternatives.
3. Call collect_variable_value for EACH variable in the list, one at a time.
   - Use the EXACT variable_name from the list.
   - Write a clear, friendly English prompt explaining what the value is for.
   - Copy any validation (allowed_values, pattern, min, max) from the list
     into the validation arg so the collector can enforce it.
   - Skip any variable named "aws_region" or "region" — these come from the environment.
4. Call generate_config.
5. Call run_terraform.
6. Call show_outputs.
7. Reply in plain text confirming success and asking if they want another resource.

## Listing and deleting (no new collect_variable_value round)
- If the user wants to see what already exists: call list_managed_resources and summarize the list in plain English (count, names, types).
- If the user wants to remove something: call delete_managed_resource with identity_name equal to the instance Name tag (EC2), bucket name (S3), or db_identifier (RDS). Then call run_terraform so the change is applied and AWS resources are destroyed. Omit module_type unless two resources share the same identity string.

## Same name vs new name (EC2 and other typed modules)
- The workspace keeps separate instances per identity field (e.g. EC2 variable `name`). Same `name` updates that instance; a different `name` adds another instance without removing the others.

## Rules
- Use ONLY variable names returned by get_module_variables. Never substitute
  or rename them (e.g. never use "ami_id" if the module declares "ami").
- Never collect aws_region or region from the user.
- One JSON object per reply — never two tool calls in one message.
- Never show raw JSON in plain-text replies to the user.
- If a tool returns an error, report it clearly in plain text and stop.
"""


class TerraformChatAgent:
    def __init__(
        self,
        modules_dir: Path,
        workspace_dir: Path,
        logger: Optional[logging.Logger] = None,
    ):
        # Resolve paths relative to this source file — works from any cwd
        base = Path(__file__).parent.parent
        self.modules_dir   = (base / modules_dir)   if not modules_dir.is_absolute()   else modules_dir
        self.workspace_dir = (base / workspace_dir) if not workspace_dir.is_absolute() else workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logger or logging.getLogger(__name__)
        self.client = Groq()

        self.registry  = ResourceRegistry(self.modules_dir)
        self.parser    = TerraformParser()
        self.generator = TerraformGenerator()
        self.executor  = TerraformExecutor(logger=self.logger)
        self.collector = InputCollector()
        self.workspace_state = WorkspaceState(self.workspace_dir)

        # Per-resource state — reset on each new resource
        self._current_module: Optional[Path] = None
        self._module_var_names: list[str]    = []   # ALL declared vars (for generator filter)
        self._required_vars: list[dict]      = []   # required vars (for LLM context injection)
        self._collected_values: dict         = {}
        self._current_resource_name: str     = ""

        # Conversation memory — rebuilt fresh each run() call
        self.memory: list[dict] = []
        self._reset_memory()

    def _reset_memory(self):
        """Initialise memory with the base system prompt (no variable names)."""
        self.memory = [{"role": "system", "content": BASE_SYSTEM_PROMPT}]

    # ─── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        resources = ", ".join(self.registry.list_resources())
        region    = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        opening   = (
            f"Hello! I can help you create AWS resources using Terraform.\n"
            f"Available resources: {resources}\n"
            f"AWS region: {region}  (set via AWS_DEFAULT_REGION)\n"
            f"Commands: list — show managed instances; delete <name> — remove by Name tag (etc.).\n\n"
            f"Which resource would you like to create?"
        )
        print(f"\n🤖  {opening}\n")
        self._append("assistant", opening)

        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "bye"):
                print("\n🤖  Goodbye!\n")
                break
            low = user_input.lower()
            if low == "help":
                print(f"\n🤖  Supported resources: {', '.join(self.registry.list_resources())}\n")
                continue
            if low in ("list", "list instances", "list ec2", "list resources"):
                self._print_managed_resources_cli()
                continue
            if low.startswith("delete "):
                ident = user_input[7:].strip()
                if ident:
                    self._cli_delete_managed_resource(ident)
                else:
                    print("\n🤖  Usage: delete <instance Name tag or other identity>\n")
                continue

            self._append("user", user_input)
            audit_log(self.logger, "user_input", {"message": user_input})
            response = self._react()
            print(f"\n🤖  {response}\n")

    # ─── ReAct loop ─────────────────────────────────────────────────────────────

    def _react(self) -> str:
        """
        Loop: call LLM → if tool call, execute and feed result back → repeat.
        Only returns when the LLM produces a plain-text (non-tool) reply.
        Raw tool JSON is NEVER returned to the caller.
        """
        for round_num in range(MAX_TOOL_ROUNDS):
            reply     = self._llm()
            tool_call = self._parse_tool(reply)

            if not tool_call:
                self._append("assistant", reply)
                return reply

            self.logger.info(
                "Round %d — tool: %s  args: %s",
                round_num + 1, tool_call["tool"], tool_call.get("args", {})
            )
            tool_result = self._dispatch(tool_call["tool"], tool_call.get("args", {}))
            self._append("assistant", reply)
            self._append("user", f"[Tool result]: {tool_result}")

        self.logger.error("Reached MAX_TOOL_ROUNDS without plain reply")
        return "I seem to be stuck. Please try again or type 'exit'."

    # ─── LLM call ───────────────────────────────────────────────────────────────

    def _llm(self) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=MAX_TOKENS,
                messages=self.memory,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            self.logger.error("Groq call failed: %s", exc)
            return f"Sorry, Groq returned an error: {exc}"

    # ─── Tool dispatch ───────────────────────────────────────────────────────────

    def _dispatch(self, tool: str, args: dict) -> str:
        audit_log(self.logger, "tool_call", {"tool": tool, "args": args})
        handlers = {
            "identify_resource":       self._tool_identify_resource,
            "get_module_variables":    self._tool_get_module_variables,
            "collect_variable_value":  self._tool_collect_variable_value,
            "generate_config":         self._tool_generate_config,
            "run_terraform":           self._tool_run_terraform,
            "show_outputs":            self._tool_show_outputs,
            "list_managed_resources":  self._tool_list_managed_resources,
            "delete_managed_resource": self._tool_delete_managed_resource,
        }
        fn = handlers.get(tool)
        if not fn:
            return f"Unknown tool: {tool}"
        try:
            return fn(**args)
        except Exception as exc:
            self.logger.exception("Tool %s raised: %s", tool, exc)
            return f"Tool error in {tool}: {exc}"

    # ─── Tool implementations ────────────────────────────────────────────────────

    def _tool_identify_resource(self, description: str) -> str:
        module_name = self.registry.resolve(description)
        if not module_name:
            return (
                f"No matching module for '{description}'. "
                f"Available: {', '.join(self.registry.list_resources())}"
            )
        self._current_module        = self.modules_dir / module_name
        self._current_resource_name = description
        self._collected_values      = {}
        self._module_var_names      = []
        self._required_vars         = []
        return f"Matched module: {module_name}  path: {self._current_module}"

    def _tool_get_module_variables(self) -> str:
        if not self._current_module:
            return "No module selected. Call identify_resource first."

        # ALL declared variable names — used by generator to filter module block
        all_declared           = self.parser.get_all_variables(self._current_module)
        self._module_var_names = [v["name"] for v in all_declared]

        # Required variables only (no default), minus region vars
        required = self.parser.get_required_variables(self._current_module)
        required = [v for v in required if v["name"] not in SKIP_VARS]
        self._required_vars = required

        # ── Dynamic context injection ────────────────────────────────────────
        # After parsing, inject a clear instruction block into the conversation
        # so the LLM knows EXACTLY which variable names and types to use.
        # This replaces any need to hardcode them in the base system prompt.
        context = self._build_variable_context(required)
        self._inject_context(context)
        # ─────────────────────────────────────────────────────────────────────

        self.logger.info(
            "Module %s: %d required vars: %s",
            self._current_module.name,
            len(required),
            [v["name"] for v in required],
        )
        return json.dumps(required, indent=2)

    def _tool_collect_variable_value(
        self,
        variable_name: str,
        prompt: str,
        var_type: str = "string",
        validation: dict = None,
    ) -> str:
        # Sanitise: strip any quotes or whitespace the LLM may have included
        # e.g. '"ami"' → 'ami',  ' instance_type ' → 'instance_type'
        variable_name = variable_name.strip().strip('"').strip("'").strip()

        # Safety guard — never collect region vars even if LLM tries
        if variable_name in SKIP_VARS:
            region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
            self._collected_values[variable_name] = region
            return f"Skipped — {variable_name} set from environment: {region!r}"

        # Guard against the LLM using a variable name not in the module
        if self._module_var_names and variable_name not in self._module_var_names:
            # Tell the LLM it used the wrong name and give it the correct list
            correct = [n for n in self._module_var_names if n not in SKIP_VARS]
            return (
                f"ERROR: '{variable_name}' is not a variable in this module. "
                f"Valid names are: {correct}. "
                f"Use the exact name from that list."
            )

        print()
        value = self.collector.collect(
            variable_name=variable_name,
            prompt=prompt,
            var_type=var_type,
            validation=validation or {},
        )
        self._collected_values[variable_name] = value
        return f"Collected {variable_name!r} = {value!r}"

    def _tool_generate_config(self) -> str:
        if not self._current_module:
            return "No module selected."

        # Verify all required vars were collected before generating
        missing = [
            v["name"] for v in self._required_vars
            if v["name"] not in self._collected_values and v["name"] not in SKIP_VARS
        ]
        if missing:
            return f"Cannot generate config — still missing values for: {missing}"

        module_type = self._current_module.name
        try:
            state, tf_key = self.workspace_state.upsert(
                module_type,
                self._collected_values,
                self._module_var_names or [],
            )
        except ValueError as exc:
            return str(exc)

        self.generator.write_workspace_from_state(
            self.workspace_dir,
            self.modules_dir,
            state,
        )
        main_path = self.workspace_dir / "main.tf"
        self.logger.debug("Generated main.tf:\n%s", main_path.read_text())
        return (
            f"Updated workspace state (for_each key {tf_key!r} under {module_type!r}). "
            f"Wrote {self.workspace_dir / 'main.tf'}, outputs.tf, terraform.tfvars."
        )

    def _migrate_after_init(self, workspace_dir: Path) -> None:
        """Move legacy module.resource EC2 address into module.ec2_instances[\"key\"]."""
        lst = self.executor.state_list(workspace_dir)
        if not lst.get("success"):
            return
        addrs = {a.strip() for a in lst["stdout"].splitlines() if a.strip()}
        legacy = "module.resource.aws_instance.this"
        if legacy not in addrs:
            return
        if any("module.ec2_instances[" in a for a in addrs):
            return
        state = self.workspace_state.load()
        ec2 = state.get("by_type", {}).get("ec2_instance") or {}
        if len(ec2) != 1:
            self.logger.warning(
                "Legacy module.resource in state but workspace has %d EC2 instance(s); "
                "skipping automatic state mv (run terraform state mv manually if needed).",
                len(ec2),
            )
            return
        tf_key = next(iter(ec2.keys()))
        to_addr = f'module.ec2_instances["{tf_key}"].aws_instance.this'
        print(f"\n  ⏳  Migrating Terraform state: {legacy} → {to_addr}")
        mv = self.executor.state_mv(workspace_dir, legacy, to_addr)
        if mv.get("success"):
            print("  ✅  State migration complete.")
        else:
            print(f"  ⚠️  State migration failed: {mv.get('stderr', mv)}")

    def _tool_run_terraform(self) -> str:
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        non_skip = {k: v for k, v in self._collected_values.items() if k not in SKIP_VARS}
        if non_skip:
            print(f"\n📋  Summary (region: {region}):\n")
            for k, v in non_skip.items():
                print(f"     {k:<30} = {v!r}")
        else:
            print(f"\n📋  Applying Terraform workspace (region: {region})…\n")

        result = self.executor.run(
            self.workspace_dir,
            after_init=self._migrate_after_init,
        )
        audit_log(self.logger, "terraform_result", {
            "resource": self._current_resource_name,
            "success":  result["success"],
        })
        if result["success"]:
            return f"Terraform apply succeeded.\n{result['stdout']}"
        return f"Terraform apply FAILED.\n{result['stderr']}"

    def _tool_show_outputs(self) -> str:
        result = self.executor.get_outputs(self.workspace_dir)
        if result["success"]:
            return f"Terraform outputs:\n{result['stdout']}"
        return f"Could not retrieve outputs: {result['stderr']}"

    def _tool_list_managed_resources(self) -> str:
        rows = self.workspace_state.list_summary()
        if not rows:
            return "No managed resources in workspace state yet."
        return json.dumps(rows, indent=2)

    def _tool_delete_managed_resource(self, identity_name: str, module_type: str = "") -> str:
        identity_name = (identity_name or "").strip()
        if not identity_name:
            return "ERROR: identity_name is required (e.g. EC2 Name tag)."
        mt = (module_type or "").strip()
        if mt:
            ok, msg = self.workspace_state.delete_by_identity(mt, identity_name)
        else:
            ok, msg = self.workspace_state.delete_any_type_by_identity(identity_name)
        if not ok:
            return msg
        state = self.workspace_state.load()
        self.generator.write_workspace_from_state(
            self.workspace_dir,
            self.modules_dir,
            state,
        )
        return (
            f"{msg} Regenerated main.tf / outputs.tf. "
            f"Call run_terraform next so Terraform destroys the removed resource in AWS."
        )

    def _print_managed_resources_cli(self) -> None:
        rows = self.workspace_state.list_summary()
        if not rows:
            print("\n🤖  No managed resources in workspace state yet.\n")
            return
        print("\n🤖  Managed resources:\n")
        for r in rows:
            ident = r.get("identity_value")
            print(
                f"   • [{r['module_type']}] {r['identity_var']}={ident!r}  "
                f"(terraform key: {r['terraform_key']})"
            )
        print()

    def _cli_delete_managed_resource(self, identity_name: str) -> None:
        ok, msg = self.workspace_state.delete_any_type_by_identity(identity_name)
        print(f"\n🤖  {msg}\n")
        if not ok:
            return
        state = self.workspace_state.load()
        self.generator.write_workspace_from_state(
            self.workspace_dir,
            self.modules_dir,
            state,
        )
        confirm = input(
            "Apply Terraform now to destroy this resource in AWS? [yes/no]: "
        ).strip().lower()
        if confirm not in ("yes", "y"):
            print("\n🤖  Skipped apply. Run the chatbot and use run_terraform when ready.\n")
            return
        result = self.executor.run(
            self.workspace_dir,
            after_init=self._migrate_after_init,
        )
        if result["success"]:
            print("\n🤖  Apply finished; resource should be destroyed in AWS.\n")
        else:
            print(f"\n🤖  Apply failed:\n{result.get('stderr', result)}\n")

    # ─── Dynamic context injection ───────────────────────────────────────────────

    def _build_variable_context(self, required_vars: list[dict]) -> str:
        """
        Build a plain-English instruction block listing the exact variable
        names, types, descriptions, and validation rules for the current module.
        Injected into the conversation after get_module_variables runs.
        """
        lines = [
            f"## Current module: {self._current_resource_name}",
            f"## Required variables — use EXACT names below, in this order:",
            "",
        ]
        for i, var in enumerate(required_vars, 1):
            name  = var["name"]
            vtype = var.get("type", "string")
            desc  = var.get("description", "")
            valid = var.get("validation", {})

            line = f"{i}. variable_name: \"{name}\"  type: {vtype}"
            if desc:
                line += f"\n   description: {desc}"
            if valid.get("allowed_values"):
                line += f"\n   allowed_values: {valid['allowed_values']}"
            if valid.get("pattern"):
                line += f"\n   pattern: {valid['pattern']}"
            if "min" in valid:
                line += f"\n   min: {valid['min']}"
            if "max" in valid:
                line += f"\n   max: {valid['max']}"
            lines.append(line)

        lines += [
            "",
            "Collect each variable above using collect_variable_value with the",
            "EXACT variable_name shown. Do not rename or skip any of them.",
        ]
        return "\n".join(lines)

    def _inject_context(self, context: str):
        """
        Inject module variable context as a system-role message.
        Using role='system' gives it higher precedence than user messages
        and prevents it from being misread as user input.
        """
        self.memory.append({
            "role":    "system",
            "content": context,
        })

    # ─── Helpers ─────────────────────────────────────────────────────────────────

    def _append(self, role: str, content: str):
        self.memory.append({"role": role, "content": content})

    def _parse_tool(self, text: str) -> Optional[dict]:
        """
        Extract a JSON tool call from LLM output.
        Handles: bare JSON, ```json fenced, JSON embedded in prose.
        """
        cleaned = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()

        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict) and "tool" in obj:
                return obj
        except json.JSONDecodeError:
            pass

        for match in re.finditer(r'\{[^{}]*\}', text, re.DOTALL):
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict) and "tool" in obj:
                    return obj
            except json.JSONDecodeError:
                pass

        return None