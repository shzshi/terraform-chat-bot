"""
AWS Terraform Chatbot — Main Entry Point
=========================================
Powered by Groq (llama-3.3-70b-versatile).
Requires: GROQ_API_KEY and AWS credentials set in environment or .env file.
"""

import sys
import os
import logging
from pathlib import Path


def _load_env():
    """Load variables from a .env file if present (no external library needed)."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Only set if not already in environment (env vars take precedence)
            if key and key not in os.environ:
                os.environ[key] = value


def _check_env():
    """Validate required environment variables before starting."""
    missing = []

    if not os.environ.get("GROQ_API_KEY"):
        missing.append(
            "  GROQ_API_KEY  — get yours free at https://console.groq.com"
        )

    # AWS credentials: accept key/secret pair OR a configured profile
    has_keys    = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
    has_profile = os.environ.get("AWS_PROFILE")
    if not has_keys and not has_profile:
        missing.append(
            "  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY  (or AWS_PROFILE)"
        )

    if missing:
        print("\n❌  Missing required environment variables:\n")
        for m in missing:
            print(m)
        print(
            "\nSet them in your shell:\n"
            "  export GROQ_API_KEY='gsk_...'\n"
            "  export AWS_ACCESS_KEY_ID='...'\n"
            "  export AWS_SECRET_ACCESS_KEY='...'\n"
            "  export AWS_DEFAULT_REGION='us-east-1'\n"
            "\nOr create a .env file in the project root (see .env.example).\n"
        )
        sys.exit(1)


def main():
    _load_env()
    _check_env()

    # Imports deferred until after env is validated so SDK init never fails
    from agent.chat_agent import TerraformChatAgent
    from utils.logger import setup_logger

    logger = setup_logger("terraform_chatbot", "logs/chatbot.log")
    logger.info("Starting AWS Terraform Chatbot (Groq backend)")

    print("\n" + "=" * 60)
    print("  🤖  AWS Infrastructure Assistant  (Powered by Groq)")
    print("=" * 60)
    print("  Type 'exit' at any time to quit.")
    print("  Type 'help' to see available resources.")
    print("  Type 'list' to see managed resources.")
    print("  Type 'delete <resource_name>' to delete a managed resource.")
    print("=" * 60 + "\n")

    agent = TerraformChatAgent(
        modules_dir=Path("terraform_modules"),
        workspace_dir=Path("terraform_workspace"),
        logger=logger,
    )

    try:
        agent.run()
    except KeyboardInterrupt:
        print("\n\n👋  Session ended. Goodbye!")
        sys.exit(0)
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        print(f"\n❌  Unexpected error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()