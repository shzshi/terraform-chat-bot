# 🤖 AWS Terraform Chatbot

An **Agentic AI chatbot** that guides users through provisioning AWS resources
using local Terraform modules — powered by Groq.

---

## Architecture

```
main.py
└── TerraformChatAgent          ← ReAct-style AI agent loop
    ├── ResourceRegistry        ← Maps user intent → module directory
    ├── TerraformParser         ← Reads variables.tf, extracts required vars
    ├── InputCollector          ← Conversational, validated variable prompts
    ├── TerraformGenerator      ← Writes main.tf + terraform.tfvars
    └── TerraformExecutor       ← Runs terraform init/plan/apply via subprocess
```

## Supported Resources

| User Says             | Module Directory              |
|-----------------------|-------------------------------|
| EC2 / server / VM     | `terraform_modules/ec2_instance` |
| S3 / bucket / storage | `terraform_modules/s3_bucket`    |
| RDS / database / SQL  | `terraform_modules/rds_instance` |
| VPC / network         | `terraform_modules/vpc`          |
| Lambda / serverless   | `terraform_modules/lambda_function` |

---

## Setup

### 1. Prerequisites

```bash
# Python 3.11+
python --version

# Terraform CLI
terraform version
```

```bash
# Create .env file

AWS_ACCESS_KEY_ID="..."
AWS_SECRET_ACCESS_KEY="..."
AWS_DEFAULT_REGION="us-east-1"
GROQ_API_KEY=""
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the chatbot

```bash
python main.py
```

---

## Example Session



---

## Adding New Terraform Modules

1. Create a directory under `terraform_modules/your_module_name/`
2. Add `variables.tf` with your input variables
3. Add `main.tf` with your resource definitions
4. Register aliases in `agent/resource_registry.py`:

```python
RESOURCE_ALIASES["your_module_name"] = [
    "your resource", "alias1", "alias2"
]
```

---

## Project Structure

```
terraform_chatbot/
├── main.py                         # Entry point
├── requirements.txt
├── agent/
│   ├── chat_agent.py               # Core ReAct agent loop
│   ├── input_collector.py          # Validated user prompts
│   └── resource_registry.py        # Intent → module mapping
├── terraform/
│   ├── parser.py                   # HCL variable extraction
│   ├── generator.py                # Config file generation
│   └── executor.py                 # Terraform CLI subprocess wrapper
├── terraform_modules/
│   ├── ec2_instance/
│   │   ├── variables.tf
│   │   └── main.tf
│   ├── s3_bucket/
│   │   ├── variables.tf
│   │   └── main.tf
│   └── rds_instance/
│       └── variables.tf
├── terraform_workspace/            # Generated configs + state (gitignore this)
│   ├── main.tf                     # Generated
│   └── terraform.tfvars            # Generated
├── logs/
│   └── chatbot.log                 # JSON-lines audit trail
└── utils/
    └── logger.py                   # Structured logging
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **GROQ as agent brain** | Handles free-text intent parsing, generates conversational questions, reasons about variable metadata |
| **ReAct-style tool dispatch** | Agent thinks → calls a tool → observes result → continues; clean separation of concerns |
| **python-hcl2 + regex fallback** | Handles both well-formed and edge-case HCL without crashing |
| **Session memory** | Full conversation history passed on every LLM call; enables multi-resource sessions |
| **Confirmation before apply** | Safety gate prevents accidental infrastructure changes |
| **Structured audit log** | JSON-lines format for easy parsing/alerting |

---
