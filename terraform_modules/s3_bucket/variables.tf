###############################################################################
# terraform_modules/s3_bucket/variables.tf
#
# Required variables (no default — chatbot will collect these):
#   - bucket_name
#   - environment
#
# Optional variables (have defaults — chatbot skips these):
#   - versioning_enabled
#   - aws_region
###############################################################################

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name (lowercase letters, numbers, hyphens, 3-63 chars)"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9\\-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Bucket name must be 3-63 characters, lowercase letters/numbers/hyphens, and cannot start or end with a hyphen."
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment for tagging: dev, staging, or prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "versioning_enabled" {
  type        = bool
  description = "Enable S3 object versioning to keep multiple versions of each object"
  default     = true
}

variable "aws_region" {
  type        = string
  description = "AWS region (injected from environment — do not collect from user)"
  default     = "us-east-1"
}
