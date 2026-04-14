###############################################################################
# terraform_modules/s3_bucket/main.tf
#
# Creates a production-ready S3 bucket with:
#   - Server-side encryption (AES-256)
#   - Public access fully blocked
#   - Versioning (enabled by default)
#   - Environment and management tags
###############################################################################

# ── Core bucket ────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name

  tags = {
    Name        = var.bucket_name
    Environment = var.environment
    ManagedBy   = "terraform-chatbot"
  }
}

# ── Block all public access ────────────────────────────────────────────────────
# Required in modern AWS accounts — prevents accidental public exposure.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Server-side encryption (AES-256) ──────────────────────────────────────────
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# ── Versioning ─────────────────────────────────────────────────────────────────
resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = var.versioning_enabled ? "Enabled" : "Suspended"
  }
}
